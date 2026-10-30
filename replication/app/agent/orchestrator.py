"""
Diagram Generation Orchestrator — coordinates the multi-agent pipeline.

SINGLE mode (component, deployment, system_context):
  Phase 0: IR View → single DiagramAgent

DEEP path (class, use_case, sequence, activity):
  Phase 0: IR View → Phase 1: PlannerAgent → Phase 2: AnalyzerAgents (parallel) → Phase 3: DiagramAgents (sequential)
  PlannerAgent identifies logical scopes.
  AnalyzerAgents read actual source files to build enriched context per scope.
  DiagramAgents generate focused diagrams from enriched context.
"""

import asyncio
import json
import logging
from app.utils.logger import get_logger
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from app.agent.base_agent import resolved_model, usage_ledger
from app.agent.ir_views import DEEP_VIEW_BYTES, SINGLE_VIEW_BYTES, generate_ir_view
from app.agent.ir_splitter import split_ir_for_diagrams
from app.agent.diagram_agent import DiagramAgent
from app.agent.corrector_agent import CorrectorAgent
from app.utils.config import settings
from app.utils.plantuml_validator import validate_puml

logger = get_logger(__name__)

# All multi-diagram types use the DEEP path:
# PlannerAgent → AnalyzerAgents (parallel) → DiagramAgents (sequential)
# This ensures each scoped agent gets its own enriched context from source code.
DEEP_ANALYSIS_TYPES = {"sequence", "activity", "class", "use_case"}

# Fast split (Python-only) is no longer used for any type — kept for reference
FAST_SPLIT_TYPES: set[str] = set()

# Types that are always single diagram
SINGLE_TYPES = {"component", "deployment", "system_context"}

# Experimental conditions (options["condition"]):
#   full          — the proposed pipeline
#   no_corrector  — ablation: compiler validation only, no CorrectorAgent
#   prefix        — ablation: compaction keeps IR order instead of importance ranking
#   single_agent  — baseline: one DiagramAgent per type, full IR (no compaction), no planning,
#                   no analysis, no correction; same model, tools and diagram-type prompt
#   single_view   — one DiagramAgent on the importance-ranked view (compaction without orchestration)
#   single_prefix — one DiagramAgent on the same-budget view in IR order (ranking ablation)
#   plain_llm     — one model call, no tools, full IR in the prompt
CONDITIONS = {"full", "no_corrector", "prefix", "single_agent", "single_view", "single_prefix", "plain_llm"}
SINGLE_CALL_CONDITIONS = {"single_agent", "single_view", "single_prefix", "plain_llm"}

# One run at a time per output directory (runs archive/overwrite files in place)
_OUTPUT_DIR_LOCKS: dict[str, asyncio.Lock] = {}

# Max parallel agents (0 = unlimited)
MAX_PARALLEL_AGENTS = int(os.environ.get("APP_MAX_PARALLEL_AGENTS", "0"))


class DiagramOrchestrator:
    """
    Orchestrates diagram generation with two speed tiers:
    - FAST: Python split → parallel DiagramAgents (no LLM planning)
    - DEEP: PlannerAgent → AnalyzerAgents → DiagramAgents (for sequence/activity)
    """

    def __init__(self, project_id: str, diagram_type: str, options: Optional[dict] = None):
        self.project_id = project_id
        self.diagram_type = diagram_type
        self.options = options or {}
        self.project_dir = Path("data") / project_id
        self.ir_path = self.project_dir / "ir_postprocessed.json"
        if not self.ir_path.exists():
            self.ir_path = self.project_dir / "ir.json"
        self.condition = self.options.get("condition", "full")
        if self.condition not in CONDITIONS:
            raise ValueError(f"Unknown condition {self.condition!r}; expected one of {sorted(CONDITIONS)}")
        self.output_dir = Path(self.options["output_dir"]) if self.options.get("output_dir") else self.project_dir / "diagrams" / diagram_type
        self._pipelined_correction_tasks: list[asyncio.Task] = []
        self.dependency_contexts = self._discover_dependency_contexts()
        self.documentation_files = self._discover_documentation()

    def _discover_dependency_contexts(self) -> list[dict]:
        """
        Discover all dependency_context.json files for this project.
        Returns a list of dicts with name, path, and summary for each dependency.
        """
        deps_dir = self.project_dir / "dependencies"
        contexts = []
        if not deps_dir.exists():
            return contexts

        for dep_dir in sorted(deps_dir.iterdir()):
            ctx_file = dep_dir / "dependency_context.json"
            if ctx_file.exists() and ctx_file.stat().st_size > 0:
                try:
                    with ctx_file.open("r", encoding="utf-8") as f:
                        ctx_data = json.load(f)
                    contexts.append({
                        "name": ctx_data.get("name", dep_dir.name),
                        "type": ctx_data.get("type", "external"),
                        "usage_type": ctx_data.get("usageType", "unknown"),
                        "summary": ctx_data.get("summary", ""),
                        "element_count": ctx_data.get("element_count", 0),
                        "path": str(ctx_file.resolve()),
                    })
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"     Skipping invalid dependency context: {ctx_file}: {e}")
        return contexts

    def _discover_documentation(self) -> list[dict]:
        """
        Discover documentation files in the project's docs/ folder.
        Returns a list of dicts with name, path, extension, and size.
        """
        docs_dir = self.project_dir / "docs"
        docs = []
        if not docs_dir.exists():
            return docs

        supported = {".pdf", ".docx", ".doc", ".md", ".markdown", ".txt"}
        for fp in sorted(docs_dir.iterdir()):
            if fp.is_file() and fp.suffix.lower() in supported:
                docs.append({
                    "name": fp.name,
                    "path": str(fp.resolve()),
                    "extension": fp.suffix.lower(),
                    "size_bytes": fp.stat().st_size,
                })
        return docs

    async def run(self) -> dict:
        lock = _OUTPUT_DIR_LOCKS.setdefault(str(self.output_dir.resolve()), asyncio.Lock())
        async with lock:
            return await self._run_locked()

    async def _run_locked(self) -> dict:
        start_time = time.time()
        self.run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        # Run record: every agent session reports usage here; scopes and phases are logged
        # so the manifest can account for planned / generated / failed work explicitly.
        self._ledger: list = []
        usage_ledger.set(self._ledger)
        self._run_record = {"phases": {}, "scopes": [], "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

        logger.info(f"")
        logger.info(f"{'═' * 70}")
        logger.info(f" ORCHESTRATOR: {self.diagram_type.upper()}")
        logger.info(f"{'═' * 70}")
        logger.info(f"   Project:  {self.project_id}")
        logger.info(f"   Type:     {self.diagram_type}")
        logger.info(f"   IR:       {self.ir_path}")
        logger.info(f"   Output:   {self.output_dir}")
        if self.dependency_contexts:
            logger.info(f"   Dependencies: {len(self.dependency_contexts)} context(s) available")
            for dep in self.dependency_contexts:
                logger.info(f"      {dep['name']} ({dep['usage_type']}, {dep['element_count']} elements)")
        else:
            logger.info(f"   Dependencies: none")
        if self.documentation_files:
            total_kb = sum(d["size_bytes"] for d in self.documentation_files) / 1024
            logger.info(f"   Documentation: {len(self.documentation_files)} file(s) ({total_kb:.0f} KB)")
            for doc in self.documentation_files:
                logger.info(f"      {doc['name']} ({doc['size_bytes'] / 1024:.1f} KB)")
        else:
            logger.info(f"   Documentation: none")

        if not self.ir_path.exists():
            return {"status": "error", "error": f"IR not found: {self.ir_path}", "output_files": [], "messages": []}

        self.output_dir.mkdir(parents=True, exist_ok=True)
        all_messages = []

        # Isolate this run: archive outputs of previous runs so they are never counted as results
        previous = [f for f in self.output_dir.iterdir()
                    if f.is_file() and (f.suffix in (".puml", ".png") or f.name == "run_manifest.json"
                                        or f.name.startswith("context_") or f.name == "diagram_plan.json"
                                        or f.name.startswith("ir_view_"))]
        previous_originals = self.output_dir / "_original"
        if previous or previous_originals.exists():
            archive = self.output_dir / "_previous_runs" / self.run_id  # unique: timestamp + random suffix
            archive.mkdir(parents=True, exist_ok=False)
            for f in previous:
                f.rename(archive / f.name)
            if previous_originals.exists():
                previous_originals.rename(archive / "_original")
            logger.info(f"    Archived {len(previous)} file(s) from a previous run → {archive}")

        # Phase 0: IR View
        logger.info(f" Phase 0: IR View...")
        # Calculate max_elements dynamically based on average element size
        # Target: IR view stays under ~60KB (~15K tokens) so the SDK Read tool can handle it
        TARGET_VIEW_BYTES = DEEP_VIEW_BYTES if self.diagram_type in DEEP_ANALYSIS_TYPES else SINGLE_VIEW_BYTES
        MIN_VIEW_ELEMENTS = 10
        MAX_VIEW_ELEMENTS = 60
        try:
            ir_size_bytes = self.ir_path.stat().st_size
            with self.ir_path.open("r") as f:
                ir_quick = json.load(f)
            total_elements = len(ir_quick.get("classes", [])) + len(ir_quick.get("functions", []))
            if total_elements > 0:
                avg_bytes_per_element = ir_size_bytes / total_elements
                view_max = int(TARGET_VIEW_BYTES / max(avg_bytes_per_element, 1))
                view_max = max(MIN_VIEW_ELEMENTS, min(MAX_VIEW_ELEMENTS, view_max))
            else:
                view_max = MAX_VIEW_ELEMENTS
            logger.info(f"   IR: {total_elements} elements, {ir_size_bytes:,} bytes "
                        f"({avg_bytes_per_element:.0f} bytes/element) → view max_elements={view_max}")
        except Exception:
            view_max = 40

        # Delete any stale cached view before regenerating with correct max_elements
        stale_view = self.output_dir / f"ir_view_{self.diagram_type}.json"
        if stale_view.exists():
            stale_view.unlink()
            logger.info(f"     Deleted stale IR view cache")

        try:
            view_path = generate_ir_view(
                self.ir_path, self.diagram_type, self.output_dir, max_elements=view_max,
                budget_bytes=DEEP_VIEW_BYTES if self.diagram_type in DEEP_ANALYSIS_TYPES else SINGLE_VIEW_BYTES,
                rank=self.condition not in ("prefix", "single_prefix"),
            )
            logger.info(f"    {view_path} ({view_path.stat().st_size:,} bytes)")
        except Exception as e:
            return {"status": "error", "error": str(e), "output_files": [], "messages": []}

        # Route based on condition and diagram type
        logger.info(f" Condition: {self.condition}")
        if self.condition == "plain_llm":
            from app.agent.plain_llm_agent import PlainLLMAgent
            logger.info(f" PLAIN-LLM BASELINE (full IR in the prompt, no tools)")
            view_path = self.ir_path
            await PlainLLMAgent(self.diagram_type).run(self.ir_path, self.output_dir)
        elif self.condition in ("single_agent", "single_view", "single_prefix"):
            if self.condition == "single_agent":
                view_path = self.ir_path  # full IR, no compaction
            logger.info(f" SINGLE-AGENT ({self.condition}): context = {view_path.name}, no planning/analysis/correction")
            agent = DiagramAgent(diagram_type=self.diagram_type)
            result = await agent.run(
                project_id=self.project_id,
                options=self._inject_context_options({**self.options, "view_path_override": str(view_path)}),
            )
            all_messages.extend(result.get("messages", []))
        elif self.diagram_type in SINGLE_TYPES:
            logger.info(f" SINGLE mode")
            await self._run_single(all_messages)
        elif self.diagram_type in FAST_SPLIT_TYPES:
            logger.info(f" FAST SPLIT mode (Python splitting, no LLM planning)")
            await self._run_fast_split(view_path, all_messages)
        elif self.diagram_type in DEEP_ANALYSIS_TYPES:
            logger.info(f" DEEP ANALYSIS mode (LLM planning + source analysis)")
            await self._run_deep(view_path, all_messages)
        else:
            logger.info(f" SINGLE mode (default)")
            await self._run_single(all_messages)

        # ── Correction Phase ──────────────────────────────────────
        # For DEEP path: corrections were pipelined — just wait for them.
        # For SINGLE/FAST paths: run batch correction now.
        logger.info(f"")
        logger.info(f"  Correction Phase: Checking generated diagrams...")

        all_correction_results: list = []

        if self._pipelined_correction_tasks:
            logger.info(f"   Waiting for {len(self._pipelined_correction_tasks)} pipelined correction(s)...")
            pipeline_results = await asyncio.gather(*self._pipelined_correction_tasks, return_exceptions=True)
            all_correction_results.extend(pipeline_results)
            self._pipelined_correction_tasks.clear()
        else:
            # SINGLE / FAST path — batch correct all .puml files now
            puml_files = [
                f for f in sorted(self.output_dir.iterdir())
                if f.is_file() and f.suffix == ".puml"
            ] if self.output_dir.exists() else []

            non_empty = [f for f in puml_files if f.stat().st_size > 0]
            empty_stubs = [f for f in puml_files if f.stat().st_size == 0]

            if empty_stubs:
                logger.info(f"   Deleting {len(empty_stubs)} empty stub(s)")
                for stub in empty_stubs:
                    stub.unlink()

            if non_empty:
                logger.info(f"   {len(non_empty)} diagram(s) to correct (batch mode)")
                batch_results = await asyncio.gather(
                    *[self._run_corrector(f) for f in non_empty],
                    return_exceptions=True,
                )
                all_correction_results.extend(batch_results)

        corrected_count = sum(1 for r in all_correction_results if isinstance(r, dict) and r.get("corrected"))
        logger.info(f"    Correction complete: {corrected_count}/{len(all_correction_results)} files corrected")

        # Metrics disabled

        # Collect output
        output_files = []
        if self.output_dir.exists():
            for f in sorted(self.output_dir.iterdir()):
                if f.is_file() and f.suffix == ".puml":
                    output_files.append(str(f))

        total = time.time() - start_time

        corrections = [r for r in all_correction_results if isinstance(r, dict)]
        correction_errors = [str(r) for r in all_correction_results if isinstance(r, Exception)]
        failed_scopes = [s for s in self._run_record["scopes"] if s["error"] or not s["generated"]]
        if not output_files:
            status = "error"
        elif failed_scopes or correction_errors:
            status = "partial"
        else:
            status = "ok"

        metrics = self._write_manifest(view_path, output_files, corrections, correction_errors, total, status)

        logger.info(f"")
        logger.info(f"{'═' * 70}")
        logger.info(f" DONE: {len(output_files)} diagrams in {total:.1f}s")
        logger.info(f"{'═' * 70}")

        return {
            "status": status,
            "error": "No diagrams were produced (see run_manifest.json)" if status == "error" else None,
            "run_id": self.run_id,
            "output_files": output_files,
            "messages": all_messages,
            "timing": {"total_seconds": round(total, 1)},
            "metrics": metrics,
        }

    def _write_manifest(self, view_path: Path, output_files: list, corrections: list,
                        correction_errors: list, total_seconds: float, status: str) -> dict:
        """Write run_manifest.json next to the diagrams: model, view telemetry, scopes,
        compiler validity before/after correction, and per-agent tokens/cost/time."""
        view_meta = {}
        try:
            view_meta = json.loads(view_path.read_text(encoding="utf-8")).get("_metadata", {})
        except Exception:
            pass

        def total(key: str) -> float:
            return sum((rec.get("usage") or {}).get(key, 0) for rec in self._ledger)

        n = len(corrections)
        summary = {
            "diagrams": len(output_files),
            "valid_before_correction": sum(1 for c in corrections if c.get("valid_before")),
            "valid_after_correction": sum(1 for c in corrections if c.get("valid_after")),
            "llm_corrections_invoked": sum(1 for c in corrections if c.get("llm_invoked")),
            "invalid_after_correction": sum(1 for c in corrections if not c.get("valid_after")),
            "scopes_planned": len(self._run_record["scopes"]),
            "scopes_failed": sum(1 for sc in self._run_record["scopes"] if sc["error"] or not sc["generated"]),
            "failed_agent_sessions": sum(1 for rec in self._ledger if rec.get("subtype") != "success"),
            "checked": n,
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "cache_read_input_tokens": total("cache_read_input_tokens"),
            "cache_creation_input_tokens": total("cache_creation_input_tokens"),
            "cost_usd": round(sum(rec.get("cost_usd", 0) for rec in self._ledger), 4),
            "agent_sessions": len(self._ledger),
            "total_seconds": round(total_seconds, 2),
        }
        manifest = {
            "run_id": self.run_id,
            "status": status,
            "project_id": self.project_id,
            "diagram_type": self.diagram_type,
            "condition": self.condition,
            "route": ("BASELINE" if self.condition in SINGLE_CALL_CONDITIONS
                      else "DEEP" if self.diagram_type in DEEP_ANALYSIS_TYPES else "SINGLE"),
            **resolved_model(),
            **self._run_record,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "ir_view": view_meta,
            "summary": summary,
            "corrections": corrections,
            "correction_errors": correction_errors,
            "agent_sessions": self._ledger,
            "output_files": [Path(f).name for f in output_files],
        }
        (self.output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        logger.info(f"    Manifest: {self.output_dir / 'run_manifest.json'} "
                    f"(valid before/after: {summary['valid_before_correction']}/{summary['valid_after_correction']} of {n}, "
                    f"${summary['cost_usd']:.2f})")
        return summary

    async def _run_corrector(self, puml_path: Path) -> dict:
        """Run one CorrectorAgent on a .puml file (validation only in no-corrector conditions)."""
        if self.condition == "no_corrector" or self.condition in SINGLE_CALL_CONDITIONS:
            check = await validate_puml(puml_path)
            return {"file": str(puml_path), "valid_before": check.valid, "valid_after": check.valid,
                    "error_before": None if check.valid else check.message, "error_after": None if check.valid else check.message,
                    "error_line_before": check.error_line, "llm_invoked": False, "corrected": False, "original_path": None}
        corrector = CorrectorAgent(diagram_type=self.diagram_type)
        return await corrector.run(puml_path=puml_path, project_id=self.project_id)

    def _inject_context_options(self, opts: dict) -> dict:
        """Inject dependency contexts and documentation paths into agent options."""
        enriched = {**opts}
        if self.dependency_contexts:
            enriched["dependency_contexts"] = self.dependency_contexts
        if self.documentation_files:
            enriched["documentation_files"] = self.documentation_files
        return enriched

    async def _run_single(self, messages: list) -> None:
        """Single diagram, no splitting."""
        agent = DiagramAgent(diagram_type=self.diagram_type)
        result = await agent.run(
            project_id=self.project_id,
            options=self._inject_context_options(self.options),
        )
        messages.extend(result.get("messages", []))

    async def _run_fast_split(self, view_path: Path, messages: list) -> None:
        """
        FAST: Smart graph-based IR splitting → sequential DiagramAgents.
        Agents run one at a time (not in parallel) because the Claude Agent SDK
        tracks file modification times per working directory: if two agents write
        to the same folder concurrently, each invalidates the other's read-stamps,
        causing endless "File has been modified since read" retry loops.
        """
        logger.info(f" Smart IR splitting...")

        groups = split_ir_for_diagrams(
            ir_path=self.ir_path,
            diagram_type=self.diagram_type,
            max_elements_per_group=25,
            min_elements_per_group=3,
        )

        # Cap the number of diagrams for very large projects
        MAX_DIAGRAMS_PER_TYPE = settings.max_diagrams_per_type
        if len(groups) > MAX_DIAGRAMS_PER_TYPE:
            logger.warning(f"     {len(groups)} groups → capped to {MAX_DIAGRAMS_PER_TYPE} most significant")
            groups = sorted(groups, key=lambda g: len(g.get("elements", [])), reverse=True)[:MAX_DIAGRAMS_PER_TYPE]

        if not groups:
            logger.info(f"   No split needed — single diagram")
            await self._run_single(messages)
            return

        logger.info(f"   {len(groups)} diagram groups → running SEQUENTIALLY to avoid SDK write-lock race")

        ok = 0
        for i, spec in enumerate(groups):
            logger.info(f"   [{i+1}/{len(groups)}] Generating: {spec['name']}")
            try:
                agent = DiagramAgent(diagram_type=self.diagram_type)
                result = await agent.run(
                    project_id=self.project_id,
                    options=self._inject_context_options({
                        **(self.options or {}),
                        "scope_name": spec["name"],
                        "scope_title": spec["title"],
                        "scope_description": spec["description"],
                        "output_filename": f"{self.diagram_type}_{spec['name']}",
                    }),
                )
                messages.extend(result.get("messages", []))
                ok += 1
            except Exception as e:
                logger.error(f"    {spec['name']}: {e}")

        logger.info(f"    {ok}/{len(groups)} diagrams generated")

    async def _run_deep(self, view_path: Path, messages: list) -> None:
        """
        DEEP: PlannerAgent → AnalyzerAgents → DiagramAgents.
        For sequence/activity where source code analysis matters.
        """
        from app.agent.planner_agent import PlannerAgent
        from app.agent.analyzer_agent import AnalyzerAgent

        semaphore = asyncio.Semaphore(MAX_PARALLEL_AGENTS) if MAX_PARALLEL_AGENTS > 0 else None

        # Phase 1: Plan
        logger.info(f" Phase 1: Planning...")
        t1 = time.time()
        planner = PlannerAgent(diagram_type=self.diagram_type)
        specs = await planner.run(self.project_id, view_path, self.ir_path, self.output_dir)
        self._run_record["phases"]["planning_s"] = round(time.time() - t1, 2)
        self._run_record["scopes"] = [{"name": s.get("name", "?"), "analyzed": False, "generated": False, "error": None} for s in specs]
        logger.info(f"     {time.time()-t1:.1f}s — {len(specs)} diagrams planned")

        if not specs:
            await self._run_single(messages)
            return

        # Phase 2: Analyze (parallel)
        logger.info(f" Phase 2: Analysis ({len(specs)} scopes)...")
        t2 = time.time()

        async def analyze(spec):
            if semaphore:
                async with semaphore:
                    a = AnalyzerAgent(self.diagram_type, spec.get("name", "?"))
                    return await a.run(self.project_id, spec, self.ir_path, self.output_dir,
                                       documentation_files=self.documentation_files)
            a = AnalyzerAgent(self.diagram_type, spec.get("name", "?"))
            return await a.run(self.project_id, spec, self.ir_path, self.output_dir,
                               documentation_files=self.documentation_files)

        ctx_results = await asyncio.gather(*[analyze(s) for s in specs], return_exceptions=True)
        valid = [(specs[i], r) for i, r in enumerate(ctx_results) if not isinstance(r, Exception)]
        for i, r in enumerate(ctx_results):
            ctx_ok = not isinstance(r, Exception) and r is not None and Path(r).exists() and Path(r).stat().st_size > 0
            self._run_record["scopes"][i]["analyzed"] = ctx_ok
            if not ctx_ok:
                self._run_record["scopes"][i]["error"] = f"analysis: {r if isinstance(r, Exception) else 'no context file written'}"
        self._run_record["phases"]["analysis_s"] = round(time.time() - t2, 2)
        logger.info(f"     {time.time()-t2:.1f}s — {len(valid)}/{len(specs)} analyzed")

        # Phase 3: Generate in parallel — DEEP path gives each agent a unique output file
        # so there is no SDK write-lock race. Corrections are pipelined immediately.
        # Set APP_MAX_DIAGRAM_CONCURRENCY in .env to tune (default 10, 0 = unlimited).
        concurrency = settings.max_diagram_concurrency if settings.max_diagram_concurrency > 0 else 999
        gen_semaphore = asyncio.Semaphore(concurrency)
        MAX_DIAGRAM_CONCURRENCY = concurrency
        logger.info(f" Phase 3: Generation ({len(valid)} diagrams) — PARALLEL (max {MAX_DIAGRAM_CONCURRENCY} concurrent)...")
        t3 = time.time()
        gen_ok = 0

        async def _generate_and_correct(spec: dict, ctx_path: Path) -> dict:
            nonlocal gen_ok
            async with gen_semaphore:
                scope_name = spec.get("name", "?")
                logger.info(f"     Starting: {scope_name}")
                try:
                    agent = DiagramAgent(diagram_type=self.diagram_type)
                    result = await agent.run(
                        project_id=self.project_id,
                        options=self._inject_context_options({
                            **(self.options or {}),
                            "scope_name": spec.get("name", ""),
                            "scope_title": spec.get("title", ""),
                            "scope_description": spec.get("description", ""),
                            "enriched_context_path": str(ctx_path),
                            "output_filename": f"{self.diagram_type}_{spec.get('name', 'diagram')}",
                        }),
                    )
                    messages.extend(result.get("messages", []))
                    output_filename = f"{self.diagram_type}_{spec.get('name', 'diagram')}"
                    produced = (self.output_dir / f"{output_filename}.puml")
                    produced_ok = produced.exists() and produced.stat().st_size > 0
                    if produced_ok:
                        gen_ok += 1
                    for scope in self._run_record["scopes"]:
                        if scope["name"] == scope_name:
                            scope["generated"] = produced_ok
                            if not produced_ok:
                                scope["error"] = "generation: no diagram written"

                    # Pipeline: immediately queue a corrector for the completed diagram
                    output_filename = f"{self.diagram_type}_{spec.get('name', 'diagram')}"
                    puml_path = self.output_dir / f"{output_filename}.puml"
                    if puml_path.exists() and puml_path.stat().st_size > 0:
                        task = asyncio.create_task(self._run_corrector(puml_path))
                        self._pipelined_correction_tasks.append(task)
                        logger.info(f"    Pipelined correction: {output_filename}.puml")

                    return result
                except Exception as e:
                    logger.error(f"     {scope_name}: {e}")
                    for scope in self._run_record["scopes"]:
                        if scope["name"] == scope_name:
                            scope["error"] = f"generation: {e}"
                    return {}

        await asyncio.gather(
            *[_generate_and_correct(s, c) for s, c in valid],
            return_exceptions=True,
        )
        self._run_record["phases"]["generation_s"] = round(time.time() - t3, 2)
        logger.info(f"     {time.time()-t3:.1f}s — {gen_ok}/{len(valid)} generated, {len(self._pipelined_correction_tasks)} corrections pipelined")
