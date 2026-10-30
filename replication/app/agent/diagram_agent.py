"""
Diagram Agent — specialized agent for generating UML diagrams from code IR.

Pipeline:
1. Pre-process: Generate a compact, diagram-specific IR view (pure Python, no LLM)
2. Agent: Claude reads the compact view + source files → generates PlantUML

This solves the large IR problem — even 50K+ token IRs get compacted to ~3-5K tokens
with only the elements relevant for the requested diagram type.
"""

import json
import logging
from app.utils.logger import get_logger
import time
from pathlib import Path
from typing import Optional

from app.agent.base_agent import BaseAgent
from app.agent.ir_views import generate_ir_view
from app.agent.prompts import get_system_prompt

logger = get_logger(__name__)


class DiagramAgent(BaseAgent):
    """
    Agent for generating UML diagrams from the project's IR.

    Pre-processes large IR files into compact diagram-specific views,
    then lets the Claude Agent SDK agent read the view + source files.
    """

    def __init__(self, diagram_type: str = "class"):
        system_prompt = get_system_prompt(diagram_type)

        super().__init__(
            name=f"DiagramAgent-{diagram_type}",
            system_prompt=system_prompt,
            tools=["Read", "Write", "Glob", "Grep"],
            permission_mode="bypassPermissions",
        )
        self.diagram_type = diagram_type
        logger.info(f"DiagramAgent initialized for '{diagram_type}' diagram")
        logger.info(f"  System prompt length: {len(system_prompt)} chars")

    async def run(
        self,
        project_id: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Generate a UML diagram for the given project."""
        start_time = time.time()
        project_dir = Path("data") / project_id
        ir_path = project_dir / "ir_postprocessed.json"
        if not ir_path.exists():
            ir_path = project_dir / "ir.json"
        opts = options or {}
        # Experiment runs write to an isolated directory per condition/repetition
        output_dir = Path(opts["output_dir"]) if opts.get("output_dir") else project_dir / "diagrams" / self.diagram_type

        # Determine scope context for logging
        scope_title = opts.get("scope_title", "full project")
        scope_name = opts.get("scope_name", "")
        is_scoped = bool(scope_name)

        logger.info(f"")
        logger.info(f"{'─' * 60}")
        logger.info(f" DIAGRAM AGENT: {self.diagram_type.upper()}")
        logger.info(f"{'─' * 60}")
        logger.info(f"   Project:      {project_id}")
        logger.info(f"   Diagram type: {self.diagram_type}")
        logger.info(f"   Scope:        {scope_title}")
        if is_scoped:
            logger.info(f"   Mode:         SCOPED (focused diagram)")
            logger.info(f"   Scope name:   {scope_name}")
            logger.info(f"   Description:  {opts.get('scope_description', '')[:100]}")
        else:
            logger.info(f"   Mode:         FULL PROJECT")
        logger.info(f"   IR path:      {ir_path} (exists: {ir_path.exists()}, {ir_path.stat().st_size:,} bytes)" if ir_path.exists() else f"   IR path:      {ir_path} (NOT FOUND)")
        logger.info(f"   Output dir:   {output_dir}")
        logger.info(f"{'─' * 60}")

        output_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 1: Generate compact IR view ──────────────────────
        logger.info(f"")
        logger.info(f" Step 1: Generating diagram-specific IR view...")
        ir_size = ir_path.stat().st_size if ir_path.exists() else 0
        logger.info(f"   Full IR size: {ir_size:,} bytes")

        # Check if orchestrator already generated a view (avoid regenerating with wrong max_elements)
        existing_view = output_dir / f"ir_view_{self.diagram_type}.json"
        try:
            if opts.get("view_path_override"):
                # Single-agent baseline: the agent receives the full IR, no compaction
                view_path = Path(opts["view_path_override"])
                logger.info(f"    Using view override (no compaction): {view_path}")
            elif existing_view.exists():
                view_path = existing_view
                view_size = view_path.stat().st_size
                logger.info(f"    Using existing IR view: {view_path} ({view_size:,} bytes)")
            else:
                # Calculate max_elements to stay within SDK Read tool limit (~60KB)
                TARGET_VIEW_BYTES = 60_000
                max_elements = 40  # safe default
                if ir_size > 0 and ir_path.exists():
                    try:
                        with ir_path.open("r") as f:
                            ir_data = json.load(f)
                        total_elements = len(ir_data.get("classes", [])) + len(ir_data.get("functions", []))
                        if total_elements > 0:
                            avg_bytes_per_element = ir_size / total_elements
                            max_elements = int(TARGET_VIEW_BYTES / max(avg_bytes_per_element, 1))
                            max_elements = max(10, min(60, max_elements))
                            logger.info(f"   IR: {total_elements} elements, avg {avg_bytes_per_element:.0f} bytes/element → max_elements={max_elements}")
                    except Exception:
                        pass

                view_path = generate_ir_view(
                    ir_path=ir_path,
                    diagram_type=self.diagram_type,
                    output_dir=output_dir,
                    max_elements=max_elements,
                )
                view_size = view_path.stat().st_size
                logger.info(f"    IR view ready: {view_path} ({view_size:,} bytes)")
                logger.info(f"    Reduction: {ir_size:,} → {view_size:,} ({view_size/max(ir_size,1)*100:.0f}%)")
                if view_size > 80_000:
                    logger.warning(f"     View still large ({view_size:,} bytes) — agent may hit SDK limit")
        except Exception as e:
            logger.error(f"    Failed to generate IR view: {e}")
            return {
                "agent": self.name,
                "project_id": project_id,
                "diagram_type": self.diagram_type,
                "messages": [{"type": "error", "message": f"IR view generation failed: {e}", "agent": self.name}],
                "output_files": [],
                "final_text": "",
            }

        # ── Step 2: Pre-create output file so SDK "read before write" check passes ──
        # Use the scoped output filename (unique per agent) to avoid race conditions
        # when multiple DiagramAgents run in parallel in the same output directory.
        output_filename = opts.get("output_filename", f"{self.diagram_type}_diagram")
        expected_puml = output_dir / f"{output_filename}.puml"

        # Only create the stub if it doesn't already have real content.
        # This prevents overwriting a previous run's output.
        if not expected_puml.exists():
            expected_puml.write_text("", encoding="utf-8")
            logger.info(f"   Pre-created output stub: {expected_puml}")
        elif expected_puml.stat().st_size == 0:
            logger.info(f"   Output stub already empty: {expected_puml}")
        else:
            logger.info(f"   Output file already has content ({expected_puml.stat().st_size:,} bytes) — agent will overwrite")

        # ── Step 3: Build prompt with view path ──────────────────
        prompt = self._build_diagram_prompt(project_id, view_path, ir_path, output_dir, options)
        logger.info(f"   Task prompt length: {len(prompt)} chars")

        # ── Step 4: Execute the agent ────────────────────────────
        logger.info(f"")
        logger.info(f" Step 4: Starting Claude Agent execution...")
        results = []
        final_text = ""
        message_count = 0
        tool_call_count = 0
        error_count = 0

        async for message in self.execute(
            prompt,
            context={
                "project_id": project_id,
                "diagram_type": self.diagram_type,
                "ir_view_path": str(view_path),
                "ir_full_path": str(ir_path),
                "output_dir": str(output_dir),
            },
        ):
            message_count += 1
            msg_type = message.get("type", "unknown")

            if msg_type == "assistant":
                text = message.get("text", "")
                tools = message.get("tool_calls", [])
                if text:
                    final_text = text
                    logger.info(f"    [{message_count}] Agent reasoning ({len(text)} chars): {text[:150]}...")
                for tc in tools:
                    tool_call_count += 1
                    tool_name = tc.get("name", "?")
                    tool_input = tc.get("input", {})
                    if tool_name in ("Read", "Write", "Edit"):
                        logger.info(f"    [{message_count}] Tool: {tool_name} → {tool_input.get('path', 'N/A')}")
                    elif tool_name in ("Glob", "Grep"):
                        logger.info(f"    [{message_count}] Tool: {tool_name} → {tool_input.get('pattern', 'N/A')}")
                    else:
                        logger.info(f"    [{message_count}] Tool: {tool_name} → {json.dumps(tool_input)[:100]}")

            elif msg_type == "tool_result":
                for tr in message.get("tool_results", []):
                    content = str(tr.get("content", ""))
                    is_error = tr.get("is_error", False)
                    if is_error:
                        logger.warning(f"     [{message_count}] Tool error: {content[:200]}")
                    else:
                        logger.info(f"     [{message_count}] Tool result ({len(content)} chars)")

            elif msg_type == "result":
                subtype = message.get("subtype", "")
                duration = message.get("duration_ms", 0)
                turns = message.get("num_turns", 0)
                cost = message.get("cost_usd", 0)
                usage = message.get("usage", {})
                logger.info(f"    [{message_count}] Result: {subtype}")
                logger.info(f"      Duration: {duration}ms, Turns: {turns}, Cost: ${cost:.4f}")
                if usage:
                    logger.info(f"      Tokens — in: {usage.get('input_tokens', 0)}, "
                                f"out: {usage.get('output_tokens', 0)}, "
                                f"total: {usage.get('total_tokens', 0)}")

            elif msg_type == "error":
                error_count += 1
                logger.error(f"    [{message_count}] Error: {message.get('message', '')}")

            elif msg_type == "system":
                logger.info(f"     [{message_count}] System: {message.get('subtype', '')}")

            results.append(message)

        # ── Step 4: Collect output files ─────────────────────────
        output_files = []
        if output_dir.exists():
            for f in output_dir.iterdir():
                if f.is_file() and f.suffix == ".puml":
                    output_files.append(str(f))
                    logger.info(f"    Output: {f.name} ({f.stat().st_size:,} bytes)")

        elapsed = time.time() - start_time
        logger.info(f"")
        logger.info(f"{'─' * 60}")
        logger.info(f" DiagramAgent Summary")
        logger.info(f"   Messages:     {message_count}")
        logger.info(f"   Tool calls:   {tool_call_count}")
        logger.info(f"   Errors:       {error_count}")
        logger.info(f"   Output files: {len(output_files)}")
        logger.info(f"   Elapsed:      {elapsed:.1f}s")
        logger.info(f"{'─' * 60}")

        return {
            "agent": self.name,
            "project_id": project_id,
            "diagram_type": self.diagram_type,
            "messages": results,
            "output_files": output_files,
            "final_text": final_text,
        }

    def _build_dependency_section(self, dependency_contexts: list[dict]) -> str:
        """Build the dependency context section for the prompt."""
        if not dependency_contexts:
            return ""

        lines = ["\n## Dependency Contexts"]
        lines.append("The following external/internal dependencies have been analyzed.")
        lines.append("Read their context files to understand their public API and integrate")
        lines.append("relevant dependency types, interfaces, and integration points into your diagram.")
        lines.append("Only include dependency elements that are actually used by the project code.\n")

        for dep in dependency_contexts:
            lines.append(f"- **{dep['name']}** ({dep['type']}, {dep['usage_type']}): "
                         f"`{dep['path']}` — {dep['summary'][:150]}")

        lines.append("\n**Integration instructions:**")
        lines.append("1. Read each dependency context file listed above")
        lines.append("2. Identify which dependency classes/interfaces are referenced by the project code")
        lines.append("3. Include those dependency elements in the diagram with a `<<dependency>>` stereotype")
        lines.append("4. Show relationships (extends, implements, uses) between project code and dependency types")
        lines.append("5. Do NOT include dependency internals that the project doesn't interact with")
        lines.append("")

        return "\n".join(lines)

    def _build_documentation_section(self, documentation_files: list[dict]) -> str:
        """Build the documentation context section for the prompt."""
        if not documentation_files:
            return ""

        # Only include text-readable docs (agents can't read PDFs/DOCX directly)
        readable = [d for d in documentation_files if d["extension"] in (".md", ".markdown", ".txt")]
        if not readable:
            return ""

        lines = ["\n## Project Documentation"]
        lines.append("The following documentation files are available for this project.")
        lines.append("Read them to understand business context, architecture decisions,")
        lines.append("domain terminology, and requirements that should inform the diagram.\n")

        for doc in readable:
            size_kb = doc["size_bytes"] / 1024
            lines.append(f"- **{doc['name']}** ({size_kb:.1f} KB): `{doc['path']}`")

        lines.append("\n**Instructions:**")
        lines.append("1. Read the documentation files above BEFORE generating the diagram")
        lines.append("2. Use domain terminology from the docs for element names and labels")
        lines.append("3. Reflect documented architecture decisions in the diagram structure")
        lines.append("4. Add notes for important business rules mentioned in the docs")
        lines.append("")

        return "\n".join(lines)

    def _build_diagram_prompt(
        self,
        project_id: str,
        view_path: Path,
        ir_path: Path,
        output_dir: Path,
        options: Optional[dict] = None,
    ) -> str:
        """Build the task prompt. Supports both full-project and scoped (orchestrator) modes."""
        opts = options or {}

        # Check if this is a scoped generation from the orchestrator
        scope_name = opts.get("scope_name", "")
        scope_title = opts.get("scope_title", "")
        scope_description = opts.get("scope_description", "")
        enriched_context = opts.get("enriched_context_path", "")
        output_filename = opts.get("output_filename", f"{self.diagram_type}_diagram")
        dependency_contexts = opts.get("dependency_contexts", [])

        documentation_files = opts.get("documentation_files", [])

        # Build dependency and documentation sections
        dep_section = self._build_dependency_section(dependency_contexts)
        doc_section = self._build_documentation_section(documentation_files)

        # Remove orchestrator keys from options display
        display_opts = {k: v for k, v in opts.items()
                        if k not in ("scope_name", "scope_title", "scope_description",
                                     "enriched_context_path", "output_filename",
                                     "dependency_contexts", "documentation_files")}
        options_str = ""
        if display_opts:
            options_str = "\n\nAdditional options:\n"
            for k, v in display_opts.items():
                options_str += f"- {k}: {v}\n"

        # Resolve all paths to absolute so the agent SDK can always find them
        abs_view = view_path.resolve()
        abs_ir = ir_path.resolve()
        abs_output = output_dir.resolve()
        project_abs = Path("data").resolve() / project_id

        # ── Shared tool rules ──────────────────────────────────────
        tool_rules = """
## IMPORTANT Tool Rules
- **Read before Write**: You MUST Read a file before Writing to it. The output .puml file already exists (empty) — Read it first, then Write the full PlantUML content.
- **Grep**: NEVER pass `-i` or any shell flags to Grep. Only use `pattern` and `path` parameters. For case-insensitive matching, use `(?i)` in the regex pattern itself.
- **Grep scope**: Always search within `data/{project_id}/` or specific files, never the entire filesystem. Ripgrep times out on broad searches.
"""

        # Scoped mode with enriched context (DEEP path — sequence/activity)
        if scope_name and enriched_context:
            abs_ctx = Path(enriched_context).resolve()
            return f"""Generate a focused {self.diagram_type} diagram for: **{scope_title}**

## Scope
- Name: {scope_name}
- Title: {scope_title}
- Description: {scope_description}

## File Locations (ABSOLUTE PATHS)
- **Enriched context** (detailed analysis — READ THIS FIRST): `{abs_ctx}`
- **IR View** (compact overview): `{abs_view}`
- **Full IR** (use Grep for extra detail): `{abs_ir}`
- **Source code files**: `{project_abs}/`
- **Output file** (READ then WRITE): `{abs_output}/{output_filename}.puml`
{tool_rules}{dep_section}{doc_section}
## Steps
1. Read the enriched context at `{abs_ctx}` — it has detailed analysis from the source code
2. Read the IR view at `{abs_view}` for additional structural context
3. {"Read the dependency context files listed above to identify integration points" if dep_section else ""}
4. Read the output file `{abs_output}/{output_filename}.puml` (required before writing)
5. Generate a FOCUSED PlantUML diagram for "{scope_title}" only — not the entire project
6. {"Include relevant dependency types with <<dependency>> stereotype where the project code uses them" if dep_section else ""}
7. Use Read on source files if you need more implementation detail
8. Write the complete PlantUML code to `{abs_output}/{output_filename}.puml`
{options_str}"""

        # Scoped mode without enriched context (FAST path — class/use_case)
        if scope_name:
            return f"""Generate a focused {self.diagram_type} diagram for: **{scope_title}**

## Scope
- Name: {scope_name}
- Title: {scope_title}
- Description: {scope_description}

## File Locations (ABSOLUTE PATHS)
- **IR View** (compact overview — READ THIS FIRST): `{abs_view}`
- **Full IR** (use Grep to find specific classes by name): `{abs_ir}`
- **Source code files**: `{project_abs}/`
- **Output file** (READ then WRITE): `{abs_output}/{output_filename}.puml`
{tool_rules}{dep_section}{doc_section}
## Steps
1. Read the IR view at `{abs_view}` — find ONLY the classes/elements belonging to "{scope_title}"
2. {"Read the dependency context files listed above to identify integration points" if dep_section else ""}
3. Read the output file `{abs_output}/{output_filename}.puml` (required before writing)
4. Generate a FOCUSED PlantUML {self.diagram_type} diagram containing ONLY the elements in "{scope_title}"
5. {"Include relevant dependency types with <<dependency>> stereotype where the project code uses them" if dep_section else "Do NOT include classes from other scopes — this diagram covers only the scope above"}
6. Write the complete PlantUML code to `{abs_output}/{output_filename}.puml`

 CRITICAL: Write ONLY to `{abs_output}/{output_filename}.puml` — do NOT write to any other file.
{options_str}"""

        # Full-project mode (single diagram, no orchestrator)
        return f"""Generate a {self.diagram_type} diagram for project "{project_id}".

## File Locations (ABSOLUTE PATHS)
- **IR View** (compact, diagram-specific — READ THIS): `{abs_view}`
- **Full IR** (large — use Grep to search specific paths only): `{abs_ir}`
- **Source code files**: `{project_abs}/`
- **Output file** (READ then WRITE): `{abs_output}/{self.diagram_type}_diagram.puml`
{tool_rules}{dep_section}{doc_section}
## Steps
1. Read the IR view file at `{abs_view}` — it contains only the elements relevant for a {self.diagram_type} diagram
2. {"Read the dependency context files listed above to understand external types used by the project" if dep_section else ""}
3. {"Read the project documentation files listed above for business context and domain terminology" if doc_section else ""}
4. Read the output file `{abs_output}/{self.diagram_type}_diagram.puml` (required before writing)
5. Analyze the elements and their relationships
6. If you need more detail on specific elements, use Grep on `{abs_ir}` only (not the whole filesystem)
7. Generate the PlantUML diagram following the syntax rules in your system instructions
8. {"Include relevant dependency types with <<dependency>> stereotype where the project code references them" if dep_section else ""}
9. Write the complete PlantUML code to `{abs_output}/{self.diagram_type}_diagram.puml`
{options_str}"""
