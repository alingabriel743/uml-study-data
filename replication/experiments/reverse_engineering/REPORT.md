# R2.5b / R1.1a / R1.1b execution report

Run date: 2026-10-27. **Partial result, not a completed four-language baseline.**
No files in `app/agent/`, `app/evaluation/evaluate.py`, or
`experiments/run_experiment.py` were edited by this work. `prepare` updated the
permitted source/IR artifacts and resolved corpus metadata.

## Executed toolchains

| Component | Exact version | Outcome |
|---|---|---|
| Python runtime | CPython 3.14.3 | existing `revision_work/.venv` |
| pyreverse | pylint 3.3.8, astroid 3.3.11 | installed locally from cached universal wheels; real class/member/inheritance fixture passes |
| Python transitive dependencies | dill 0.4.0, isort 6.0.1, mccabe 0.7.0, platformdirs 4.13.5, tomlkit 0.13.3 | pinned in `requirements.txt` |
| Java reference | OpenJDK 26.0.2.1, Homebrew build 26.0.2.1 | custom javac AST adapter; no target build or dependency resolution |
| UML syntax compiler | PlantUML 1.2026.8, commit 149874a | shopping-cart compiles |
| Node / npm | 22.19.0 / 11.6.2 | already installed |
| tplant | 3.1.3 requested in `package.json` | installation fails: `ENOTFOUND registry.npmjs.org`; no resolved lock and no TS/JS result |
| PHP / Composer / ph-puml | not installed | PHP corpus references unavailable; adapter not integration-tested |

The javac adapter is independent of Tree-sitter and IR, but its serializer and
edge-resolution rules are our own code. **It is not an established Java RE
product**, so it supports independent extractor validation and a provisional
deterministic condition without fully satisfying R2.5b's established-tool
comparison. `uml-java-doclet` documents a JDK 8 restriction; the installed JDK
is 26. Source-only `java2plantuml` was identified but could not be downloaded.

## Priority projects

| Project | Pinned source | Reference result |
|---|---|---|
| shopping-cart | `30fcead06d85c04a93b277d696b9efaa279673b7` | success; 37 class-like declarations, 6 internal relations |
| PHPMailer | `56740234fc503e8a8001bfd9154c44174d686b0a` | real PHPMailer snapshot prepared; missing PHP runtime/tool |
| laravel-ai | unavailable | prepare fails resolving `github.com` |
| GenericAgent | unavailable | prepare fails resolving `github.com` |

Local cached GenericAgent and impeccable clones were inspected as a possible
offline source. They contain only shallow September 2026 commits and no reachable
pre-July commit. They were **not** substituted for the corpus snapshots.

All twelve project IDs now have `data/<id>/reference/reference_manifest.json`.
For the ten unprepared projects, manifests identify missing snapshots; for
PHPMailer, missing PHP. There are no scores for these failures. Only
shopping-cart has current `reference/class/` output. The validation JSON and CSV
contain one successful project and eleven `unavailable` projects.

## Extractor validation: shopping-cart

Precision = matches / IR facts; recall = matches / independent reference facts.

| Category | IR | Reference | Matched | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| Class-like declarations | 37 | 37 | 37 | 1.000 | 1.000 |
| Direct internal inheritance / realization | 6 | 6 | 6 | 1.000 | 1.000 |
| Declared attribute names, scoped to owner | 57 | 57 | 57 | 1.000 | 1.000 |
| Declared method names, scoped to owner | 200 | 200 | 200 | 1.000 | 1.000 |

No ambiguous normalized names were excluded on this project. Constructor
overloads and method overloads collapse by name. javac reported 25 parent
clauses; **19 external/unresolved clauses are outside the scored internal-edge
scope**. Thus 6/6 is not a claim to have validated all inheritance clauses.

The latest recorded reference generation took **0.397 seconds**. That includes
source hashing, JDK version check, Java source launcher compilation, parsing,
serialization and compatibility checks. It excludes source preparation, IR
extraction, installation, UML compilation and evaluation. See the manifest for
per-command timing; do not treat this single timing as a throughput result.

## Baseline evaluation and checks

The native output layout is compatible with `app.evaluation.evaluate`:

* 1 class-diagram file; PlantUML compiler validity 1.000.
* Parser counts: 37 elements, 6 relationships, no parser warnings.
* Against the existing IR: entity recall, grounded-element rate,
  relationship precision against IR and generalization precision are all 1.000.
* No correction or LLM was used. This is a whole-snapshot class diagram with
  name-only members and declared internal edges, not an architectural summary.

Machine-readable evidence is in `results/shopping-cart/{files.csv,batches.csv,summary.json}`
and `results/extractor_validation.{json,csv}`. These are different comparisons:
the former evaluates the baseline against IR; the latter evaluates IR against
independent reference facts.

Validation checks: **22 passed, 2 skipped**. Real pyreverse and javac integrations
pass, including repeated output hashes, missing Java dependencies, syntax-error
rejection, stale-reference detection and failed-rerun archival. TS and plain-JS
tests are explicitly skipped because tplant was not installed. There is no
evidence yet that tplant is robust on the corpus's plain JS. No PHP integration
success is claimed.

The first local UML compiler invocation omitted `-Djava.awt.headless=true` and
the JDK process exited 134 without diagnostics. Re-running through Homebrew's
`plantuml` wrapper (which supplies that flag) compiled successfully; current
evaluation files contain the successful run. This was an invocation problem,
not corrected diagram syntax.

## Threats-to-validity text supported by this implementation

1. Deterministic-tool outputs are fallible silver references, not expert ground
   truth. Their disagreement with IR requires inspection. The custom Java
   adapter also introduces experimenter choices despite using an independent
   compiler parser; an established external baseline is still required.
2. Only one small Java project currently has a corpus result. The complete
   agreement cannot establish cross-language extractor validity or performance
   at scale. Missing references are excluded with explicit N/A values.
3. The shared public normalizer removes namespaces, punctuation, case and
   generics. Ambiguous short names are disclosed and excluded, reducing scored
   coverage. Unreported collisions would overstate agreement.
4. Member scores are name-level, owner-scoped sets. They do not validate types,
   signatures, overloads, visibility or implicit/generated members. Constructors
   count as declared methods. Correct names can coexist with incorrect types.
5. External ancestry and unresolved internal symbols are not assessed by the
   internal-edge metric. The javac adapter does not perform semantic attribution;
   its associations indicate field-type references rather than composition.
6. Python inference, decorated properties, dynamic attributes, PHP traits and
   promotion, and JS prototype/factory construction have tool-specific coverage.
   Pyreverse may classify properties as attributes where our IR uses methods.
   Plain JS and modern PHP compatibility remain unverified here.
7. Source scope includes tests and examples remaining in the prepared snapshots.
   This is reproducible but can differ from an expert's production architecture
   scope. Class references do not validate behavioral or deployment diagrams.
8. The deterministic reference avoids LLM circularity, but evaluation of the
   baseline against the same IR being validated is only an agreement measure.
   Expert annotation remains necessary. These results do not address training
   data contamination or justify corpus selection by themselves.

## Remaining work

Restore shell network access; prepare the missing pinned snapshots; install and
lock tplant; install a compatible PHP runtime and a modern PHP generator; run
an established Java RE generator or explicitly justify retaining the javac
adapter as a different deterministic baseline. Then run all language fixtures,
the priority projects and remaining corpus, regenerate validation/evaluation
reports and review mismatches. R1.1a expert ground truth and corpus selection
remain outside this implementation.
