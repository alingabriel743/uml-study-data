# Code2UML replication package

## Contents

| Folder | Content |
|---|---|
| `app/` | Pipeline (IR extractors, IR views, Planner/Analyzer/Diagram/Corrector agents, orchestrator) and the offline evaluator (`app/evaluation/`). Agent prompts are in `app/agent/prompts.py`. |
| `experiments/` | Experiment drivers: `run_experiment.py` (prepare, generate, evaluate), `setup_v2_cases.py`, `analyze.py` (all tables and summary values), `figures.py`, `corrector_retro.py` and `corrector_preservation.py` (correction of the archived files that do not compile), `reverse_engineering/` (deterministic reference tools and extractor validation). |
| `experiments/results/` | Per-run table of status, files, compiler verdicts, tokens, time, agent sessions and cost (`tables/runs.csv`), evaluator output per file and per repository and diagram type, the tables of Section 4 (`tables/`), the figures and `summary_full.json`. |
| `data/<repository>/` | Corrected IR (`ir_postprocessed.json`), deterministic reference diagrams (`reference/`) and generated diagrams of every condition and repetition (`runs/<condition>/rep<k>/<type>/`). Uncorrected originals of repaired files are kept in `_original/`. |
| `release/corrected-corpus/` | Pinned commits, per-repository counts and the corrected IRs of the twelve repositories. |
| `release/audits/` | Extraction and relationship audits with their adjudication. |
| `release/runs/` | Cost and metric summaries of the Mindustry replication block (Section 4.7). |
| `release/validation/` | Compiler checks and per-case metrics of the archived cohort; `release/archived_plantuml_check.tsv` holds the compiler verdict for each archived file. |
| `release/extraction/` | Scripts that pin the archival-cutoff commits and rebuild the corrected corpus and its IRs. |
| `tests/` | Parser, evaluator, IR-view and reverse-engineering tests. |

The archived diagrams are the top-level language folders of this repository (`../Java`, `../JavaScript`, `../PHP`,
`../Python`), which are left unchanged. The scripts for the archived cohort expect them in
`uml-study-data-main/`, so run `ln -s .. uml-study-data-main` inside this folder before using them. Source
code of the studied repositories is not redistributed. `experiments/setup_v2_cases.py` clones each repository at the
commit listed in `release/corrected-corpus/pinned_revisions.csv`.

## Recomputing the results (no model calls)

```bash
pip install -r requirements.txt          # Python 3.10+, PlantUML 1.2026.8 on PATH for compiler checks
python -m experiments.run_experiment evaluate  # evaluator over every run directory
python experiments/analyze.py                  # tables/*.csv and summary_full.json
python -m experiments.corrector_preservation   # structure preservation of corrected archived files
python -m pytest -q tests
```

## Re-running generation (model calls, billed)

Generation uses Claude models on Amazon Bedrock through the Claude Agent SDK. Set `AWS_BEARER_TOKEN_BEDROCK` and
`AWS_REGION` (or the equivalent AWS credentials) in the environment or in a `.env` file.

```bash
python -m experiments.setup_v2_cases                           # sources at pinned commits and IRs
python -m experiments.run_experiment generate \
    --projects v2-Mindustry v2-evolver v2-composer v2-lyra \
    --types class sequence activity use_case \
    --conditions single_view single_prefix single_agent plain_llm --reps 3 \
    --model-id us.anthropic.claude-sonnet-4-6 --max-cost-usd 60
```

`--model-id` selects the Bedrock model or inference profile (the base model for the comparison was Claude Sonnet
4.6, with Claude Opus 4.6 and Claude Sonnet 4.5 as alternatives), and `--max-cost-usd` is a hard cap on the recorded
cost of one invocation. Each new run writes a local manifest (not included in this package) with the model identifier, the agent sessions, the tokens
(including prompt-cache reads and writes), the wall-clock time and the cost reported by the provider. Model outputs
are not deterministic, so re-runs reproduce the measured distributions rather than the identical files.
