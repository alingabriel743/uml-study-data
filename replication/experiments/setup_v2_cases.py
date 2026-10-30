"""
Prepare revision experiment cases from the v2 corrected corpus.

For each project: check out the v2 archival-cutoff commit (pinned_revisions.csv) into
data/v2-<project>/src and install the v2 corrected IR as data/v2-<project>/ir_postprocessed.json,
rewriting its source paths to the local checkout. The v2 IR is the one every v2 run used,
so new conditions are directly comparable with the v2 results.

  python -m experiments.setup_v2_cases --projects PHPMailer composer
"""

import argparse
import csv
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V2 = ROOT / "release" if (ROOT / "release").exists() else Path.home() / "Desktop" / "Code2UML_v2_submission" / "successor-release"
PINS = V2 / "corrected-corpus" / "pinned_revisions.csv"
IR_TAR = V2 / "corrected-corpus" / "ir-postprocessed.tar.gz"


def setup(project: str) -> dict:
    pin = next(r for r in csv.DictReader(PINS.open()) if r["project"] == project)
    case = ROOT / "data" / f"v2-{project}"
    src = case / "src"
    case.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        local = ROOT / "data" / project / "src"  # reuse an existing clone when available
        origin = str(local) if local.exists() else pin["url"]
        subprocess.run(["git", "clone", "--quiet", origin, str(src)], check=True)
        if local.exists():
            subprocess.run(["git", "remote", "set-url", "origin", pin["url"]], cwd=src, check=True)
    commit = pin["archival_cutoff_commit"]
    if subprocess.run(["git", "cat-file", "-e", commit], cwd=src).returncode != 0:
        subprocess.run(["git", "fetch", "--quiet", "origin"], cwd=src, check=True)
    subprocess.run(["git", "checkout", "--quiet", commit], cwd=src, check=True)

    member = f"ir/{pin['language']}/{project}/ir_postprocessed.json"
    with tarfile.open(IR_TAR) as tar:
        ir = json.load(tar.extractfile(member))
    prefix = f"corpus/repos/{pin['language']}/{project}/"
    local_prefix = f"data/v2-{project}/src/"
    n = 0
    for key in ("classes", "functions", "types", "tables", "procedures"):
        for item in ir.get(key, []) or []:
            sf = item.get("source_file", "")
            if sf.startswith(prefix):
                item["source_file"] = local_prefix + sf[len(prefix):]
                n += 1
    ir.setdefault("metadata", {})["revision_case"] = {"project": project, "commit": commit,
                                                      "source": "v2 corrected corpus (archival cutoff)"}
    (case / "ir_postprocessed.json").write_text(json.dumps(ir), encoding="utf-8")
    return {"project": project, "commit": commit, "paths_rewritten": n,
            "ir_bytes": (case / "ir_postprocessed.json").stat().st_size}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projects", nargs="+", required=True)
    for p in ap.parse_args().projects:
        print(setup(p))


if __name__ == "__main__":
    main()
