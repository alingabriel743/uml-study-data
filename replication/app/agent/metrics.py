"""
Diagram Generation Metrics — computes quality and performance metrics.

5 visible metrics returned in API response:
  1. Code Coverage (%) — elements in diagrams / total IR elements
  2. Generation Time (s) — wall-clock total
  3. Diagram Complexity Score — elements × (1 + relationships/elements)
  4. Syntax Validity Rate (%) — diagrams passing correction / total
  5. Token Efficiency — diagram elements per 1K tokens used

1 logged-only metric:
  6. Cost per Diagram ($) — from ResultMessage total_cost_usd
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── PlantUML parsing helpers ────────────────────────────────────

# Keywords that define elements in PlantUML
ELEMENT_KEYWORDS = [
    "class ", "interface ", "abstract ", "enum ", "actor ", "usecase ",
    "participant ", "component ", "node ", "database ", "rectangle ",
    "cloud ", "artifact ", "package ", "folder ", "frame ",
    "queue ", "stack ", "card ", "collections ",
]

# Patterns for relationships (arrows)
RELATIONSHIP_PATTERNS = [
    r"[A-Za-z_]\w*\s*(?:--|>|\.\.>|-->|<--|<\.\.|\.\.|--|->|<-|\.>|<\.)",
    r"[A-Za-z_]\w*\s+(?:o--|*--|<\|--|--\|>|--o|--\*)",
]


def count_plantuml_elements(plantuml_code: str) -> int:
    """Count the number of elements (classes, actors, components, etc.) in PlantUML code."""
    count = 0
    for line in plantuml_code.split("\n"):
        stripped = line.strip()
        if any(stripped.startswith(kw) for kw in ELEMENT_KEYWORDS):
            count += 1
    return count


def count_plantuml_relationships(plantuml_code: str) -> int:
    """Count the number of relationships (arrows) in PlantUML code."""
    count = 0
    for line in plantuml_code.split("\n"):
        stripped = line.strip()
        # Skip directives, comments, empty lines
        if not stripped or stripped.startswith("'") or stripped.startswith("@") or stripped.startswith("!"):
            continue
        # Check for arrow patterns
        if any(arrow in stripped for arrow in ["-->", "<--", "..>", "<..", "--|>", "<|--",
                                                 "--o", "o--", "--*", "*--", "->", "<-",
                                                 ".>", "<.", "--", ".."]):
            # Filter out lines that are just element definitions with braces
            if not stripped.endswith("{") and not stripped.endswith("}"):
                # Must contain at least two identifiers connected by an arrow
                if re.search(r'\w+\s*(?:--|>|\.\.>|-->|->|<--|<-|<\.\.|\.>|<\.)', stripped):
                    count += 1
    return count


def count_ir_elements(ir_path: Path) -> int:
    """Count total elements in the IR JSON file."""
    try:
        with ir_path.open("r", encoding="utf-8") as f:
            ir_data = json.load(f)
        total = 0
        for key in ["classes", "functions", "tables", "procedures", "types",
                     "interfaces", "enums", "structs"]:
            total += len(ir_data.get(key, []))
        return max(total, 1)  # avoid division by zero
    except Exception as e:
        logger.warning(f"Failed to count IR elements: {e}")
        return 1


# ── MetricsCollector ────────────────────────────────────────────

class MetricsCollector:
    """
    Collects data during diagram generation and computes metrics at the end.

    Usage:
        collector = MetricsCollector(ir_path)
        # ... generation happens ...
        collector.add_diagram(plantuml_code, filename)
        collector.add_correction_result(was_corrected=False)
        collector.add_agent_result(cost_usd=0.05, input_tokens=1000, output_tokens=500)
        collector.set_timing(total_seconds=45.2)
        metrics = collector.compute()
    """

    def __init__(self, ir_path: Path):
        self.ir_path = ir_path
        self.total_ir_elements = count_ir_elements(ir_path)

        # Collected during generation
        self._diagrams: list[dict] = []  # {elements, relationships, filename}
        self._correction_results: list[bool] = []  # True = needed correction
        self._agent_costs: list[float] = []
        self._agent_tokens: list[dict] = []  # {input_tokens, output_tokens}
        self._total_seconds: float = 0.0

    def add_diagram(self, plantuml_code: str, filename: str = "") -> None:
        """Register a generated diagram for metrics."""
        elements = count_plantuml_elements(plantuml_code)
        relationships = count_plantuml_relationships(plantuml_code)
        self._diagrams.append({
            "elements": elements,
            "relationships": relationships,
            "filename": filename,
        })

    def add_correction_result(self, was_corrected: bool) -> None:
        """Register a correction result (True = corrections were needed)."""
        self._correction_results.append(was_corrected)

    def add_agent_result(
        self,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Register agent execution results (cost and tokens)."""
        self._agent_costs.append(cost_usd)
        self._agent_tokens.append({
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })

    def set_timing(self, total_seconds: float) -> None:
        """Set the total generation time."""
        self._total_seconds = total_seconds

    def compute(self) -> dict:
        """
        Compute all metrics and return as a dictionary.

        Returns:
            {
                "code_coverage_pct": float,
                "generation_time_seconds": float,
                "diagram_complexity_score": float,
                "syntax_validity_pct": float,
                "token_efficiency": float,
                "total_diagrams": int,
                "total_elements_in_diagrams": int,
                "total_ir_elements": int,
            }
        """
        # ── 1. Code Coverage (%) ──
        total_diagram_elements = sum(d["elements"] for d in self._diagrams)
        code_coverage = min(
            (total_diagram_elements / self.total_ir_elements) * 100,
            100.0,
        )

        # ── 2. Generation Time (s) ──
        generation_time = round(self._total_seconds, 1)

        # ── 3. Diagram Complexity Score ──
        # Per diagram: elements × (1 + relationships/elements)
        # Average across all diagrams
        complexity_scores = []
        for d in self._diagrams:
            e = d["elements"]
            r = d["relationships"]
            if e > 0:
                score = e * (1 + r / e)
                complexity_scores.append(score)
            else:
                complexity_scores.append(0)
        avg_complexity = (
            round(sum(complexity_scores) / len(complexity_scores), 1)
            if complexity_scores
            else 0.0
        )

        # ── 4. Syntax Validity Rate (%) ──
        # Diagrams that did NOT need correction / total
        total_checked = len(self._correction_results)
        if total_checked > 0:
            valid_count = sum(1 for c in self._correction_results if not c)
            syntax_validity = round((valid_count / total_checked) * 100, 1)
        else:
            syntax_validity = 100.0  # no corrections run = assume valid

        # ── 5. Token Efficiency (elements per 1K tokens) ──
        total_tokens = sum(
            t["input_tokens"] + t["output_tokens"]
            for t in self._agent_tokens
        )
        if total_tokens > 0:
            token_efficiency = round(
                total_diagram_elements / (total_tokens / 1000),
                2,
            )
        else:
            token_efficiency = 0.0

        # ── 6. Cost per Diagram ($) — LOGGED ONLY ──
        total_cost = sum(self._agent_costs)
        num_diagrams = len(self._diagrams)
        cost_per_diagram = total_cost / num_diagrams if num_diagrams > 0 else 0.0

        # Log cost (not returned in API)
        logger.info(f"")
        logger.info(f"{'═' * 60}")
        logger.info(f" GENERATION METRICS")
        logger.info(f"{'═' * 60}")
        logger.info(f"  Code Coverage:         {code_coverage:.1f}%")
        logger.info(f"  Generation Time:       {generation_time}s")
        logger.info(f"  Complexity Score:      {avg_complexity}")
        logger.info(f"  Syntax Validity:       {syntax_validity}%")
        logger.info(f"  Token Efficiency:      {token_efficiency} elements/1K tokens")
        logger.info(f"  ─── Cost (logged only) ───")
        logger.info(f"  Total Cost:            ${total_cost:.4f}")
        logger.info(f"  Cost per Diagram:      ${cost_per_diagram:.4f}")
        logger.info(f"  Total Tokens:          {total_tokens:,}")
        logger.info(f"  Diagrams Generated:    {num_diagrams}")
        logger.info(f"  IR Elements:           {self.total_ir_elements}")
        logger.info(f"  Diagram Elements:      {total_diagram_elements}")
        logger.info(f"{'═' * 60}")

        return {
            "code_coverage_pct": round(code_coverage, 1),
            "generation_time_seconds": generation_time,
            "diagram_complexity_score": avg_complexity,
            "syntax_validity_pct": syntax_validity,
            "token_efficiency": token_efficiency,
            "total_diagrams": num_diagrams,
            "total_elements_in_diagrams": total_diagram_elements,
            "total_ir_elements": self.total_ir_elements,
        }
