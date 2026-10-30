"""
Analyzer Agent — Phase 2 of the multi-agent pipeline.

Given a specific diagram scope (e.g., "User Authentication Flow" with key elements
["AuthController", "UserService"]), this agent reads the relevant source files
and produces an enriched context JSON with the detailed information needed to
generate a focused diagram.

Multiple AnalyzerAgents run in PARALLEL for different scopes.
"""

import json
import logging
from pathlib import Path

from app.agent.base_agent import BaseAgent

logger = logging.getLogger(__name__)


ANALYZER_SYSTEM_PROMPT = """
You are a code analysis expert. Your job is to read source code files and
extract the detailed information needed to generate a UML diagram for a
specific scope/workflow.

You will be given:
1. A diagram scope (name, description, key elements, source files)
2. Access to the project's source files and IR

Your task:
1. Read the relevant source files using Read tool
2. Analyze the code to extract:
   - For class diagrams: full class structures, relationships, dependencies
   - For sequence diagrams: method call chains, interactions, data flow
   - For activity diagrams: workflow steps, conditions, branching logic
   - For component diagrams: module dependencies, interfaces
3. Write an enriched context JSON file with your findings

## Output Format — COMPACT SUMMARY (1,000-2,000 tokens max)
Write a CONCISE JSON file. The goal is a dense, high-signal summary — NOT a full code dump.

```json
{
  "scope": "User Authentication Flow",
  "diagram_type": "sequence",
  "key_participants": ["User", "AuthController", "UserService", "TokenManager", "Database"],
  "interaction_flow": [
    "User → AuthController: POST /login(username, password)",
    "AuthController → UserService: authenticate(username, password)",
    "UserService → Database: findUser(username)",
    "AuthController → TokenManager: createToken(userId)",
    "AuthController → User: return AuthResponse(token)"
  ],
  "key_relationships": [
    "AuthController depends_on UserService",
    "AuthController depends_on TokenManager",
    "UserService reads Database"
  ],
  "important_notes": "JWT tokens, 1h expiry. AuthController is @RestController at /api/auth",
  "source_files_analyzed": ["src/auth/AuthController.java", "src/auth/UserService.java"]
}
```

## Rules — Context Engineering Principles
- READ source files but return only HIGH-SIGNAL summaries (not raw code)
- Extract real call chains, not guesses — trace the actual flow
- For sequence: focus on the message flow between participants
- For class: focus on inheritance, composition, key attributes
- Keep your output COMPACT: 1,000-2,000 tokens max
- If a file is too large, use Grep to find the relevant sections
- Prefer flat lists over nested structures to reduce token count
"""


class AnalyzerAgent(BaseAgent):
    """Agent that reads source files and extracts detailed context for a specific diagram scope."""

    def __init__(self, diagram_type: str, scope_name: str):
        super().__init__(
            name=f"AnalyzerAgent-{scope_name}",
            system_prompt=ANALYZER_SYSTEM_PROMPT,
            tools=["Read", "Write", "Glob", "Grep"],
            permission_mode="bypassPermissions",
        )
        self.diagram_type = diagram_type
        self.scope_name = scope_name

    async def run(
        self,
        project_id: str,
        diagram_spec: dict,
        ir_path: Path,
        output_dir: Path,
        documentation_files: list[dict] | None = None,
    ) -> Path:
        """
        Analyze source files for a specific diagram scope.

        Args:
            project_id: Project identifier
            diagram_spec: From PlannerAgent: {name, title, description, key_elements, source_files, scope}
            ir_path: Path to full IR
            output_dir: Where to write enriched context

        Returns:
            Path to the enriched context JSON file
        """
        context_path = output_dir / f"context_{diagram_spec['name']}.json"

        # Build the scope description for the prompt
        elements_str = ", ".join(diagram_spec.get("key_elements", [])[:15])
        files_str = "\n".join(f"  - `{f}`" for f in diagram_spec.get("source_files", [])[:10])
        if not files_str:
            files_str = "  (Use Glob to find relevant files)"

        logger.info(f"")
        logger.info(f"{'─' * 60}")
        logger.info(f" ANALYZER AGENT: {self.scope_name.upper()}")
        logger.info(f"{'─' * 60}")
        logger.info(f"   Project:        {project_id}")
        logger.info(f"   Diagram type:   {self.diagram_type}")
        logger.info(f"   Scope name:     {self.scope_name}")
        logger.info(f"   Scope title:    {diagram_spec.get('title', '?')}")
        logger.info(f"   Description:    {diagram_spec.get('description', '')[:100]}")
        logger.info(f"   Key elements:   {elements_str[:100]}")
        logger.info(f"   Source files:   {', '.join(diagram_spec.get('source_files', [])[:5])}")
        logger.info(f"   Context output: {context_path}")
        logger.info(f"{'─' * 60}")
        logger.info(f"")
        logger.info(f"🔎 Step 1: Analyzing source files for scope '{self.scope_name}'...")

        # Resolve to absolute paths
        abs_ir = ir_path.resolve()
        abs_ctx = context_path.resolve()
        abs_src = (Path("data") / project_id).resolve()

        # Build documentation section if docs are available
        doc_lines = ""
        readable_docs = [d for d in (documentation_files or []) if d.get("extension") in (".md", ".markdown", ".txt")]
        if readable_docs:
            doc_lines = "\n## Project Documentation\nRead these for business context and domain terminology:\n"
            for doc in readable_docs:
                doc_lines += f"- `{doc['path']}`\n"

        prompt = f"""Analyze the source code for this specific diagram scope.

## Scope
- Name: {diagram_spec.get('name', '')}
- Title: {diagram_spec.get('title', '')}
- Description: {diagram_spec.get('description', '')}
- Key elements: {elements_str}
- Scope: {diagram_spec.get('scope', '')}

## Suggested source files to read (check absolute path: {abs_src}/):
{files_str}

## Project (ABSOLUTE PATHS)
- Project ID: {project_id}
- Source files are in: `{abs_src}/`
- Full IR (use Grep for element details): `{abs_ir}`
{doc_lines}
## Task
1. Read the suggested source files (or use Glob pattern `{abs_src}/**/*` to find them)
2. {"Read the project documentation above for business context" if doc_lines else ""}
3. Analyze the code to extract detailed information for a {self.diagram_type} diagram
4. Write the enriched context to `{abs_ctx}`

Focus on extracting:
- Actual method signatures and call chains
- Real relationships (inheritance, composition, dependencies)
- Flow logic (for sequence/activity diagrams)
- Interface definitions and boundaries
- {"Domain terminology and business rules from documentation" if doc_lines else ""}
"""

        results = []
        logger.info(f"📡 Step 2: Executing analysis agent...")
        async for message in self.execute(
            prompt,
            context={
                "project_id": project_id,
                "diagram_type": self.diagram_type,
                "scope": self.scope_name,
                "phase": "analysis",
            },
        ):
            results.append(message)

        logger.info(f"")
        logger.info(f" Step 3: Checking analysis output...")
        if context_path.exists():
            size = context_path.stat().st_size
            logger.info(f" Enriched context written: {context_path} ({size:,} bytes)")
            # Try to summarize what was found
            try:
                with context_path.open("r") as f:
                    ctx = json.load(f)
                elements = ctx.get("elements", [])
                interactions = ctx.get("interactions", [])
                logger.info(f"   Elements found:      {len(elements)}")
                logger.info(f"   Interactions found:  {len(interactions)}")
                if elements:
                    logger.info(f"   Key elements: {', '.join(e.get('name', '?') for e in elements[:5])}")
            except Exception:
                pass
        else:
            logger.warning(f"  No context file generated for {self.scope_name}")

        return context_path
