"""
Plain-LLM baseline — one model call, no tools, no agents.

The full post-processed IR is placed directly in the prompt (the model has a 1M-token
context window), together with the same diagram-type system prompt the DiagramAgent
uses. The response's PlantUML blocks are written to the output directory. This is the
"plain Claude Sonnet 4.6 given the full IR" baseline requested in review (R1.4, R1.3c).
"""

import re
import time
from pathlib import Path

from app.agent.base_agent import BaseAgent
from app.agent.prompts import get_system_prompt
from app.utils.logger import get_logger

logger = get_logger(__name__)

PLANTUML_BLOCK_RE = re.compile(r"@startuml[\s\S]*?@enduml")

PLAIN_SYSTEM_PROMPT = """You are a software architecture expert who writes UML diagrams in PlantUML.
You will receive the full intermediate representation (IR) of a source-code repository,
followed by instructions for one diagram type. Follow those instructions exactly."""

NO_TOOLS_NOTICE = """

## Baseline mode (overrides any tool instructions above)
You have NO tools: you cannot read, search or write files. The complete intermediate
representation (IR) of the project is included in the user message. Produce the diagram(s)
directly in your answer as PlantUML, each enclosed in @startuml ... @enduml. If the project
warrants several focused diagrams of this type, output each one separately.
"""


class PlainLLMAgent(BaseAgent):
    def __init__(self, diagram_type: str):
        super().__init__(
            name=f"PlainLLM-{diagram_type}",
            # Same system prompt for every diagram type, IR first in the user message:
            # the (large) IR prefix is cached across diagram types and repetitions.
            system_prompt=PLAIN_SYSTEM_PROMPT,
            tools=[],
            permission_mode="default",
        )
        self.diagram_type = diagram_type

    async def run(self, ir_path: Path, output_dir: Path) -> dict:
        start = time.time()
        ir_text = ir_path.read_text(encoding="utf-8")
        prompt = (f"## Full IR ({len(ir_text):,} characters)\n```json\n{ir_text}\n```\n\n"
                  f"## Instructions for this request\n{get_system_prompt(self.diagram_type)}{NO_TOOLS_NOTICE}\n"
                  f"Generate {self.diagram_type.replace('_', ' ')} diagram(s) for this project.")

        final_text, errors = "", []
        async for message in self.execute(prompt, context={"diagram_type": self.diagram_type, "phase": "plain_llm"}):
            if message.get("type") == "assistant" and message.get("text"):
                final_text += message["text"] + "\n"
            elif message.get("type") == "error":
                errors.append(message.get("message", ""))

        output_dir.mkdir(parents=True, exist_ok=True)
        blocks = PLANTUML_BLOCK_RE.findall(final_text)
        files = []
        for i, block in enumerate(blocks, 1):
            path = output_dir / f"{self.diagram_type}_plain_{i}.puml"
            path.write_text(block.strip() + "\n", encoding="utf-8")
            files.append(str(path))
        (output_dir / "plain_llm_response.md").write_text(final_text, encoding="utf-8")

        logger.info(f"   PlainLLM: {len(blocks)} diagram(s), {len(ir_text):,} chars of IR, {time.time() - start:.1f}s"
                    + (f", errors: {errors}" if errors else ""))
        return {"output_files": files, "errors": errors}
