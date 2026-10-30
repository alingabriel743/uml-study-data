"""
PlantUML Corrector Agent — Post-generation syntax correction.

Compiles a generated .puml file with the official PlantUML parser. Only files that fail
to compile are sent to the LLM, together with the compiler's error line and message; the
result is compiled again. The pre-correction original is preserved for evaluation.

Uses the same diagram-specific rules as the v1 corrector but via Claude Agent SDK.
"""

import logging
from app.utils.logger import get_logger
import time
from pathlib import Path
from typing import Optional

from app.agent.base_agent import BaseAgent
from app.utils.plantuml_validator import validate_puml

logger = get_logger(__name__)


# ── Diagram-specific syntax rules (ported from v1) ──────────────

BASE_RULES = """
## Universal PlantUML Syntax Rules
- Always include `@startuml` and `@enduml` tags
- Never use the `device` keyword — use `node` instead
- Only one `title` directive, placed after `@startuml`
- Valid PlantUML identifier naming (alphanumeric + underscore)
- Quoted strings for labels with spaces or special characters
- Braces must be balanced

## Project Style Conventions (NOT syntax errors — never edit a file only to apply these)
- Avoid `skinparam linetype ortho` (valid PlantUML, but produces cluttered layouts here)
- Avoid duplicate `skinparam` declarations
"""

DIAGRAM_RULES = {
    "class": """
## Class Diagram Rules
- `class ClassName {` with proper braces
- `+public`, `-private`, `#protected`, `~package` visibility modifiers
- `ClassName --|> ParentClass` inheritance arrows
- `abstract class AbstractName` for abstract classes
- `interface IName` for interfaces
- `enum EnumName { VALUE1, VALUE2 }` for enumerations
- `{static} +staticMethod()` for static members
- `{abstract} +abstractMethod()` for abstract methods
- Do not group elements at the end — relationships inline with classes
""",

    "sequence": """
## Sequence Diagram Rules
- `participant "Name" as Alias` declarations
- `actor ActorName` for human actors
- `Alias -> Target : message` synchronous calls
- `Alias --> Target : return` returns
- `activate` / `deactivate` lifeline management
- `alt` / `else` / `end` alternative flows
- `loop` / `end` loops
- Do NOT use `and` between elements — invalid syntax
- Style convention (not a syntax error): avoid `<<stereotypes>>` on participants
""",

    "use_case": """
## Use Case Diagram Rules
- `actor "Name" as Alias` for actors
- `usecase (Use Case) as UC` for use cases
- `rectangle "System" { }` for system boundary
- `Alias --> UC` for associations
- `UC ..> UC : <<include>>` for includes
- `UC <.. UC : <<extend>>` for extensions
- Do NOT use `!define RECTANGLE_COLOR` or custom color definitions
""",

    "activity": """
## Activity Diagram Rules
- `start` and `stop` endpoints are mandatory
- `:Activity Description;` action syntax
- `if (condition) then (yes)` / `else (no)` / `endif` for decisions
- `elseif (condition) then (yes)` — NOT `else if`
- `while (condition) is (yes)` / `endwhile (no)` loops
- `fork` / `fork again` / `end fork` parallel flows
- `|swimlane|` partitioning — do NOT use `|#Color|Name|` inline colors
- Colors for swim lanes: `|#AntiqueWhite|Lane Name|` at lane declaration
- `continue` keyword is NOT supported — remove it
""",

    "component": """
## Component Diagram Rules
- `component "Name" as Alias` component definitions
- `package "Package" { }` containers
- `database "DB" as DBAlias` databases
- `cloud "Cloud" as CloudAlias` cloud services
- `Comp --> Interface` dependencies
- Do NOT group components inside `database { }` blocks
- Do NOT include `!define` color definitions
- Remove any title that repeats the project name
""",

    "deployment": """
## Deployment Diagram Rules
- `node "Name" as Alias { }` execution environments
- `artifact "Name" as Alias` deployable units
- `database "DB" as Alias` data stores
- `cloud "Service" as Alias` cloud services
- `NodeAlias --> ArtifactAlias : <<deploy>>` deployment
- Style convention (not a syntax error): quote relationship labels
- Do NOT use `device` keyword — replace with `node`
- Define all elements INSIDE packages, not outside
- Do NOT use `rectangle` for grouping — use `node` or `package`
- Do NOT include external icons
""",

    "system_context": """
## System Context Diagram Rules
- `rectangle "System" as Alias { }` main system boundary
- `actor "Actor" as Alias` external actors
- `rectangle "External System" as Alias` external systems
- `database "DB" as Alias` external databases
- `Alias --> System : "interaction"` interactions
- `system` keyword does NOT exist — use `rectangle`
- Do NOT use `!define` color definitions
- Do NOT group rectangles in sections
- Do NOT include external icons
""",
}


def _get_rules(diagram_type: str) -> str:
    return BASE_RULES + DIAGRAM_RULES.get(diagram_type, "")


class CorrectorAgent(BaseAgent):
    """
    Post-generation PlantUML syntax corrector.
    Reads a .puml file, applies diagram-specific rules, rewrites if needed.
    """

    def __init__(self, diagram_type: str):
        rules = _get_rules(diagram_type)

        system_prompt = f"""You are a PlantUML syntax expert specializing in {diagram_type} diagrams.

Your job: a PlantUML file FAILED to compile. Fix the reported syntax error(s) and write the corrected version back.

{rules}

## Instructions
1. Read the PlantUML file provided
2. Locate the compiler error reported in the task (line number and message)
3. Fix ONLY what prevents compilation — preserve ALL meaning, relationships, and structure
4. Write the corrected PlantUML to the SAME file path

## What NOT to change
- Element names, elements, relationships, associations, labels
- Business logic and semantic content (never add or remove elements or relationships)
- Styling, layout, and colors — style conventions are not reasons to edit
"""

        super().__init__(
            name=f"CorrectorAgent-{diagram_type}",
            system_prompt=system_prompt,
            tools=["Read", "Write"],
            permission_mode="bypassPermissions",
        )
        self.diagram_type = diagram_type

    async def run(
        self,
        puml_path: Path,
        project_id: Optional[str] = None,
    ) -> dict:
        """
        Compile a PlantUML file; if it fails, correct it with the LLM and compile again.

        Args:
            puml_path: Absolute path to the .puml file to correct
            project_id: Optional project ID for logging

        Returns:
            dict with valid_before, valid_after, corrected (content changed),
            llm_invoked, compiler errors, and the path of the preserved original
        """
        start_time = time.time()
        abs_path = puml_path.resolve()

        logger.info(f"")
        logger.info(f"{'─' * 60}")
        logger.info(f"  CORRECTOR AGENT: {self.diagram_type.upper()}")
        logger.info(f"{'─' * 60}")
        logger.info(f"   File:    {abs_path}")

        if not abs_path.exists():
            logger.error(f"    File not found: {abs_path}")
            return {"corrected": False, "error": f"File not found: {abs_path}"}

        original = abs_path.read_bytes()
        before = await validate_puml(abs_path)

        result = {
            "file": str(abs_path),
            "valid_before": before.valid,
            "error_before": None if before.valid else before.message,
            "error_line_before": before.error_line,
            "llm_invoked": False,
            "corrected": False,
            "valid_after": before.valid,
            "error_after": None if before.valid else before.message,
            "original_path": None,
        }

        if before.valid:
            result["elapsed"] = time.time() - start_time
            logger.info(f"    Compiles — no correction needed ({result['elapsed']:.1f}s)")
            return result

        # Preserve the uncorrected original for evaluation (pre/post comparisons)
        original_dir = abs_path.parent / "_original"
        original_dir.mkdir(exist_ok=True)
        original_path = original_dir / abs_path.name
        original_path.write_bytes(original)
        result["original_path"] = str(original_path)

        location = f"line {before.error_line}" if before.error_line else "unknown line"
        logger.info(f"    Compile error at {location}: {before.message}")

        prompt = f"""Fix the syntax error in this PlantUML file.

File path: `{abs_path}`

## PlantUML compiler output
- Error at {location}
- Message: {before.message}

## IMPORTANT Tool Rules
- You MUST Read the file before Writing to it (SDK requirement).

## Steps
1. Read the file at `{abs_path}`
2. Fix the error reported by the compiler (and any other syntax errors it would hit next)
3. Write the corrected content back to `{abs_path}`

Remember: preserve ALL elements, relationships, names, and semantic meaning. Fix ONLY syntax."""

        results = []
        async for message in self.execute(
            prompt,
            context={
                "diagram_type": self.diagram_type,
                "puml_file": str(abs_path),
                "phase": "correction",
            },
        ):
            results.append(message)

        after = await validate_puml(abs_path)
        result.update({
            "llm_invoked": True,
            "corrected": abs_path.read_bytes() != original,
            "valid_after": after.valid,
            "error_after": None if after.valid else after.message,
            "elapsed": time.time() - start_time,
        })

        status = "fixed" if after.valid else "still invalid"
        logger.info(f"    Correction {status} ({result['elapsed']:.1f}s)")
        return result
