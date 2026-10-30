#!/usr/bin/env python3
"""Compare manuscript, archived, and reconstructed corpus size claims."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


MANUSCRIPT_IR_ENTITIES = {
    ("Java", "shopping-cart"): 31,
    ("Java", "Mindustry"): 1135,
    ("Java", "debezium"): 2293,
    ("JavaScript", "evolver"): 607,
    ("JavaScript", "impeccable"): 200,
    ("JavaScript", "puter"): 1297,
    ("PHP", "laravel-ai"): 415,
    ("PHP", "PHPMailer"): 84,
    ("PHP", "composer"): 552,
    ("Python", "GenericAgent"): 155,
    ("Python", "openai-agents"): 4578,
    ("Python", "lyra"): 942,
}


def archived_metrics(archive_root: Path) -> dict[tuple[str, str], set[int]]:
    values: dict[tuple[str, str], set[int]] = {}
    for path in archive_root.glob("*/*/*/metrics.json"):
        language, project = path.relative_to(archive_root).parts[:2]
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("total_ir_elements")
        if isinstance(value, int):
            values.setdefault((language, project), set()).add(value)
    return values


def archived_source_evidence(archive_root: Path, language: str, project: str) -> list[str]:
    samples: list[str] = []
    for path in sorted((archive_root / language / project).glob("*/ir_view_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        metadata = payload.get("_metadata", {})
        for sample in metadata.get("source_files_sample", []):
            if sample not in samples:
                samples.append(sample)
            if len(samples) == 5:
                return samples
    return samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--reconstructed-summary", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()

    archived = archived_metrics(args.archive_root)
    reconstructed_payload = json.loads(args.reconstructed_summary.read_text(encoding="utf-8"))
    reconstructed = {
        (row["language"], row["project"]): row for row in reconstructed_payload["results"]
    }

    rows = []
    for key, manuscript_value in MANUSCRIPT_IR_ENTITIES.items():
        language, project = key
        archived_values = sorted(archived.get(key, set()))
        rebuilt = reconstructed[key]
        rebuilt_value = rebuilt["processed_counts"]["total"]
        source_sample = archived_source_evidence(args.archive_root, language, project)
        mislabeled = project == "PHPMailer" and any(
            marker in sample.lower()
            for sample in source_sample
            for marker in ("ddtrace", "datadog", "wordpress", "drupal")
        )
        rows.append(
            {
                "language": language,
                "project": project,
                "manuscript_ir_entities": manuscript_value,
                "archived_metrics_ir_entities": archived_values[0] if len(archived_values) == 1 else None,
                "archived_metric_values": archived_values,
                "reconstructed_source_files": rebuilt["source_files"],
                "reconstructed_ir_entities": rebuilt_value,
                "manuscript_minus_archived": (
                    manuscript_value - archived_values[0] if len(archived_values) == 1 else None
                ),
                "reconstructed_minus_archived": (
                    rebuilt_value - archived_values[0] if len(archived_values) == 1 else None
                ),
                "archived_label_integrity": "mislabeled" if mislabeled else "not_flagged",
                "archived_source_sample": source_sample,
            }
        )

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    fieldnames = [
        "language",
        "project",
        "manuscript_ir_entities",
        "archived_metrics_ir_entities",
        "reconstructed_source_files",
        "reconstructed_ir_entities",
        "manuscript_minus_archived",
        "reconstructed_minus_archived",
        "archived_label_integrity",
        "archived_source_sample",
    ]
    with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            csv_row = {name: row[name] for name in fieldnames}
            csv_row["archived_source_sample"] = " | ".join(row["archived_source_sample"])
            writer.writerow(csv_row)


if __name__ == "__main__":
    main()
