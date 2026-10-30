"""
DependencyAnalyzerAgent — uses Claude Agent SDK to analyze extracted dependency source code
and produce a structured dependency_context.json readable by the diagram agents.

Replaces the v1 Weaviate vectorization approach. Instead of embedding code chunks into a
vector store for retrieval, the agent reads the dependency source directly (via the SDK's
Read/Glob/Grep built-in tools), reasons over it, and writes a compact JSON context file
that describes the dependency's public API surface, key types, and integration points.

The produced file is stored at:
    data/{project_id}/dependencies/{dep_name}/dependency_context.json

Diagram agents can then Read that file when generating diagrams that involve this dependency.
"""

import json
from pathlib import Path
from typing import Any

from app.agent.base_agent import BaseAgent
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior software architect tasked with analysing a third-party or
internal dependency's source code and producing a concise, structured JSON context file.

Your output will be used by UML diagram generation agents that need to understand how the
dependency integrates with the main project — they will NOT have access to the raw dependency
source themselves, only the context file you produce.

## What to produce
Analyse the dependency source files available to you and write a JSON file at the path
specified in the task. The JSON must follow this exact schema:

{
  "name": "<dependency name>",
  "type": "<internal | external>",
  "usageType": "<e.g. backendFramework, databaseConnector, ...>",
  "detected_language": "<primary programming language>",
  "summary": "<2–4 sentence description of what this dependency does>",
  "public_api": [
    {
      "name": "<class / interface / function / module name>",
      "kind": "<class | interface | function | module | enum>",
      "file": "<relative file path>",
      "description": "<one-line description>",
      "methods": ["<method signature>"],
      "extends": ["<parent class/interface>"],
      "implements": ["<interface>"]
    }
  ],
  "key_types": [
    {
      "name": "<type / data model name>",
      "kind": "<class | interface | enum | struct>",
      "file": "<relative file path>",
      "fields": ["<field: type>"]
    }
  ],
  "integration_points": [
    {
      "description": "<how the main project would typically integrate with this>",
      "entry_classes": ["<class name>"],
      "configuration_files": ["<file>"]
    }
  ],
  "element_count": <total number of public_api + key_types entries>,
  "source_files_scanned": <number of source files you read>
}

## Rules
- Focus on PUBLIC API surfaces — classes, interfaces, exported functions, DTOs.
- Ignore test files, build scripts, auto-generated code, and vendor subdirectories.
- Keep each description to one sentence — be precise, not verbose.
- If you cannot determine a field, use null, not an empty string.
- Write ONLY valid JSON to the output file — no markdown fences, no prose.
- After writing, verify the file is valid JSON by reading it back.
- Do not leave any temporary files behind.
"""


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------


class DependencyAnalyzerAgent(BaseAgent):
    """
    Analyses a dependency's source files using Claude Agent SDK built-in tools
    and writes a structured dependency_context.json.

    Tools used: Read, Write, Glob, Grep
    """

    def __init__(self) -> None:
        super().__init__(
            name="DependencyAnalyzerAgent",
            system_prompt=_SYSTEM_PROMPT,
            tools=["Read", "Write", "Glob", "Grep"],
            permission_mode="acceptEdits",
        )

    async def run(
        self,
        *,
        dependency_name: str,
        dependency_type: str,
        usage_type: str,
        source_path: Path,
        output_path: Path,
    ) -> dict[str, Any]:
        """
        Run the dependency analysis agent.

        Args:
            dependency_name:  Human-readable dependency name (e.g. "Spring Framework").
            dependency_type:  "internal" or "external".
            usage_type:       One of the USAGE_TYPES constants.
            source_path:      Absolute path to the dependency's source files.
            output_path:      Absolute path where dependency_context.json should be written.

        Returns:
            dict with keys: status, output_file, element_count, error (on failure)
        """
        logger.info(
            f"DependencyAnalyzerAgent.run: name={dependency_name!r}, "
            f"source={source_path}, output={output_path}"
        )

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        prompt = self._build_analysis_prompt(
            dependency_name=dependency_name,
            dependency_type=dependency_type,
            usage_type=usage_type,
            source_path=source_path,
            output_path=output_path,
        )

        result_message: dict[str, Any] | None = None
        last_error: str | None = None

        async for message in self.execute(prompt=prompt):
            if message.get("type") == "result":
                result_message = message
            elif message.get("type") == "error":
                last_error = message.get("message", "Unknown error")

        # Validate the output file was written and is valid JSON
        if output_path.exists():
            try:
                with output_path.open("r", encoding="utf-8") as fh:
                    context_data = json.load(fh)
                element_count = context_data.get("element_count", 0)
                logger.info(
                    f"DependencyAnalyzerAgent: wrote context for {dependency_name!r} "
                    f"({element_count} elements) → {output_path}"
                )
                return {
                    "status": "success",
                    "output_file": str(output_path),
                    "element_count": element_count,
                    "cost_usd": result_message.get("cost_usd", 0) if result_message else 0,
                }
            except json.JSONDecodeError as exc:
                logger.error(
                    f"DependencyAnalyzerAgent: output file is not valid JSON: {exc}"
                )
                return {
                    "status": "failed",
                    "output_file": str(output_path),
                    "element_count": 0,
                    "error": f"Agent wrote invalid JSON: {exc}",
                }
        else:
            error = last_error or "Agent did not write the output file"
            logger.error(f"DependencyAnalyzerAgent: {error}")
            return {
                "status": "failed",
                "output_file": str(output_path),
                "element_count": 0,
                "error": error,
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_analysis_prompt(
        self,
        *,
        dependency_name: str,
        dependency_type: str,
        usage_type: str,
        source_path: Path,
        output_path: Path,
    ) -> str:
        return f"""Analyse the dependency described below and write its context file.

## Dependency metadata
- Name:        {dependency_name}
- Type:        {dependency_type}
- Usage type:  {usage_type}
- Source path: {source_path}
- Output file: {output_path}

## Instructions
1. Use Glob to discover source files under {source_path}
   (look for *.java, *.py, *.ts, *.js, *.cs, *.cpp, *.kt, *.php, *.sql — skip test/ and vendor/ dirs).
2. Use Read to inspect the most important source files (focus on entry points, interfaces, DTOs).
   Prioritise files at the top level of the source tree and any files named *Service*, *Client*,
   *Repository*, *Model*, *Dto*, *Interface*, *Api*, *Controller*, *Config*.
3. Use Grep if you need to find specific patterns (e.g., `public class`, `export interface`).
4. Synthesise your findings into the JSON schema described in your system prompt.
5. Write the resulting JSON (no markdown, pure JSON) to: {output_path}
6. Read {output_path} back to confirm it is valid JSON.

Be thorough but efficient — scan enough files to build a representative picture of the public API,
then write the output. Do not read every single file if the dependency is large.
"""
