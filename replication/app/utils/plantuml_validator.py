"""
PlantUML Validator — independent syntax check using the official PlantUML compiler.

Runs `plantuml -syntax` on a file and reports whether it compiles. Used by the
CorrectorAgent (to decide whether correction is needed and to verify the result)
and by the offline evaluator. The LLM is never the judge of its own syntax.
"""

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ValidationResult:
    valid: bool
    return_code: int
    message: str
    error_line: Optional[int] = None


def _parse_syntax_output(output: str) -> tuple[bool, Optional[int], str]:
    """Parse `plantuml -syntax` output.

    Valid:   '<DIAGRAM TYPE>\n(<n> entities)'
    Invalid: 'ERROR\n<line>\n<message>'  (line is PlantUML's 0-based index)
    """
    lines = output.splitlines()
    if not lines or lines[0].strip() != "ERROR":
        return True, None, output
    error_line = None
    if len(lines) > 1:
        try:
            error_line = int(lines[1].strip()) + 1  # convert to 1-based
        except ValueError:
            pass
    message = " ".join(l.strip() for l in lines[2:]) or "Syntax error"
    return False, error_line, message


async def validate_puml(puml_path: Path) -> ValidationResult:
    """Run the PlantUML parser in `-syntax` mode (no image written). Return code 0 = valid."""
    cmd = shlex.split(settings.plantuml_command) + ["-syntax"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(puml_path.read_bytes()),
            timeout=settings.plantuml_timeout_seconds,
        )
    except FileNotFoundError:
        logger.error(f"    PlantUML not found: {settings.plantuml_command!r} (set APP_PLANTUML_COMMAND)")
        raise
    except asyncio.TimeoutError:
        proc.kill()
        return ValidationResult(valid=False, return_code=-1, message="PlantUML timed out")

    output = stdout.decode("utf-8", errors="replace").strip()
    parsed_ok, error_line, message = _parse_syntax_output(output)
    return ValidationResult(
        valid=proc.returncode == 0 and parsed_ok,
        return_code=proc.returncode,
        message=message,
        error_line=error_line,
    )
