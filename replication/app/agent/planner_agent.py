"""
Planner Agent — Phase 1 of the multi-agent pipeline.

Reads the compact IR view and identifies:
- For class diagrams: logical modules/packages to split into separate diagrams
- For sequence diagrams: distinct workflows/flows (e.g., login, order processing)
- For activity diagrams: key processes/pipelines to diagram
- For other types: sensible groupings

Outputs a JSON plan that the orchestrator uses to spawn parallel agents.
"""

import json
import logging
from app.utils.logger import get_logger
from pathlib import Path

from app.agent.base_agent import BaseAgent

logger = get_logger(__name__)


PLANNER_SYSTEM_PROMPT = """
You are a software architecture analyst. Your job is to analyze a project's
Intermediate Representation (IR) and identify the distinct diagrams that should
be generated.

You will be given:
1. An IR view file (compact JSON with project structure)
2. The type of diagram being requested

Your task:
1. Read the IR view file
2. Identify logical groupings for separate diagrams
3. Write a JSON plan file with the list of diagrams to generate

## Output Format
Write a JSON file with this structure:
```json
{
  "diagram_type": "sequence",
  "diagrams": [
    {
      "name": "user_authentication_flow",
      "title": "User Authentication Flow",
      "description": "Login, token validation, and session management",
      "key_elements": ["AuthController", "UserService", "TokenManager"],
      "source_files": ["src/auth/controller.java", "src/auth/service.java"],
      "scope": "Authentication module"
    },
    {
      "name": "order_processing_flow",
      "title": "Order Processing Flow",
      "description": "Order creation, payment, and fulfillment",
      "key_elements": ["OrderController", "PaymentService", "InventoryManager"],
      "source_files": ["src/order/controller.java"],
      "scope": "Order processing pipeline"
    }
  ]
}
```

## Scaling Rules — Number of Diagrams Based on Project Size
Scale the number of diagrams to the project size:
- **Tiny** (<20 classes): 1-3 diagrams
- **Small** (20-100 classes): 3-6 diagrams
- **Medium** (100-500 classes): 6-12 diagrams
- **Large** (500-2000 classes): 10-20 diagrams
- **Very large** (2000+ classes): 15-30 diagrams (group by major subsystem/module)

The goal is FULL COVERAGE: every meaningful class/workflow in the project should appear in at least one diagram.

## Quality Rules
- Each diagram should be focused and readable (5-20 key elements max)
- Names should be descriptive and use snake_case
- For class diagrams: group by package/module/layer — one diagram per logical module
- For use case diagrams: group by actor or functional area
- For sequence diagrams: one diagram per main user-facing workflow (entry point → response)
- For activity diagrams: one diagram per key orchestrator/pipeline/process
- **KEEP EACH ENTRY COMPACT** — this is a navigation map, not an analysis
- Each diagram entry: name + 1-line description + 3-5 key_elements + 1-3 source_files
- The AnalyzerAgents will do the deep reading — your job is just to identify WHAT to analyze
"""


class PlannerAgent(BaseAgent):
    """Agent that analyzes IR and creates a multi-diagram plan."""

    def __init__(self, diagram_type: str):
        super().__init__(
            name=f"PlannerAgent-{diagram_type}",
            system_prompt=PLANNER_SYSTEM_PROMPT,
            tools=["Read", "Write", "Glob", "Grep"],
            permission_mode="bypassPermissions",
        )
        self.diagram_type = diagram_type

    async def run(
        self,
        project_id: str,
        view_path: Path,
        ir_path: Path,
        output_dir: Path,
    ) -> list[dict]:
        """
        Analyze the project and return a list of diagram specs.

        Returns:
            List of diagram specs: [{name, title, description, key_elements, source_files, scope}]
        """
        plan_path = output_dir / "diagram_plan.json"

        logger.info(f"")
        logger.info(f"{'─' * 60}")
        logger.info(f" PLANNER AGENT: {self.diagram_type.upper()}")
        logger.info(f"{'─' * 60}")
        logger.info(f"   Project:      {project_id}")
        logger.info(f"   Diagram type: {self.diagram_type}")
        logger.info(f"   IR View:      {view_path} ({view_path.stat().st_size:,} bytes)" if view_path.exists() else f"   IR View:      {view_path} (NOT FOUND)")
        logger.info(f"   Full IR:      {ir_path}")
        logger.info(f"   Plan output:  {plan_path}")
        logger.info(f"{'─' * 60}")
        logger.info(f"")
        logger.info(f" Step 1: Reading IR view to identify diagrams...")

        # Resolve to absolute paths
        abs_view = view_path.resolve()
        abs_ir = ir_path.resolve()
        abs_plan = plan_path.resolve()
        abs_src = (Path("data") / project_id).resolve()

        # Count total IR elements to guide diagram count scaling
        total_ir_elements = 0
        try:
            with ir_path.open("r", encoding="utf-8") as f:
                ir_data = json.load(f)
            for key in ["classes", "functions", "tables", "procedures", "types", "interfaces", "enums"]:
                total_ir_elements += len(ir_data.get(key, []))
        except Exception:
            pass

        logger.info(f"   IR elements for scaling: {total_ir_elements}")

        prompt = f"""Analyze this project and create a diagram generation plan.

## Project
- Project ID: {project_id}
- Diagram type: {self.diagram_type}
- **Total IR elements: {total_ir_elements}** — use this to scale the number of diagrams (see Scaling Rules)
- IR View (READ THIS): `{abs_view}`
- Full IR (use Grep if needed): `{abs_ir}`
- Source files are in: `{abs_src}/`

## Task
1. Read the IR view at `{abs_view}`
2. Identify the distinct {self.diagram_type} diagrams that should be generated (scale to project size!)
3. Write the plan to `{abs_plan}`

Write the plan as JSON following the format in your instructions.
Apply the Scaling Rules: with {total_ir_elements} IR elements, generate the appropriate number of diagrams.
"""

        results = []
        logger.info(f" Step 2: Executing planning agent...")
        async for message in self.execute(
            prompt,
            context={
                "project_id": project_id,
                "diagram_type": self.diagram_type,
                "phase": "planning",
            },
        ):
            results.append(message)

        # Read the plan file
        logger.info(f"")
        logger.info(f" Step 3: Reading generated plan...")
        if plan_path.exists():
            try:
                with plan_path.open("r") as f:
                    plan = json.load(f)
                diagrams = plan.get("diagrams", [])
                logger.info(f" Plan created: {len(diagrams)} diagrams identified")
                for i, d in enumerate(diagrams, 1):
                    logger.info(f"   {i}. {d.get('name', '?')}: {d.get('title', '?')}")
                    logger.info(f"      Scope: {d.get('scope', '')}")
                    logger.info(f"      Elements: {', '.join(d.get('key_elements', [])[:5])}")
                    logger.info(f"      Files: {', '.join(d.get('source_files', [])[:3])}")
                return diagrams
            except Exception as e:
                logger.error(f" Failed to read plan: {e}")

        logger.warning(f"  No plan file generated. Using single-diagram fallback.")
        return [{
            "name": self.diagram_type,
            "title": f"{self.diagram_type.replace('_', ' ').title()} Diagram",
            "description": f"Complete {self.diagram_type} diagram for the project",
            "key_elements": [],
            "source_files": [],
            "scope": "entire project",
        }]
