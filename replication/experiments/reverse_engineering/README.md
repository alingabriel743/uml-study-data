# Deterministic reverse engineering and extractor validation

Supports R2.5b and the deterministic-reference portion of R1.1a/R1.1b.
No LLM, project imports, project builds, or calls to our Tree-sitter extractors
occur in reference generation. `prepare` remains the source of corpus snapshots
and the IR being tested.

**Current result: partial, blocked by shell network access.** Shopping-cart has
an independently parsed Java reference and full extractor agreement on the
reported name-level facts. Its Java diagram uses a custom adapter over javac,
not an established third-party reverse-engineering product. Consequently this
is a usable independent reference and a provisional deterministic baseline;
it does **not** finish the requested established-tool baseline comparison.
The other corpus projects have explicit failure manifests, not empty diagrams
or fabricated scores. See [REPORT.md](REPORT.md) and [CLAUDE_HANDOFF.md](CLAUDE_HANDOFF.md).

## Run

From the repository root:

```sh
revision_work/.venv/bin/python -m experiments.run_experiment prepare --projects shopping-cart PHPMailer laravel-ai GenericAgent
revision_work/.venv/bin/python -m experiments.reverse_engineering.run --projects shopping-cart PHPMailer laravel-ai GenericAgent
revision_work/.venv/bin/python -m experiments.reverse_engineering.validate_extractor
```

Omit `--projects` to cover the whole corpus. The baseline runner continues after
individual failures, writes every manifest, and exits 1 if any project failed.
The preparation runner may abort at the first failed clone; prepare available
projects individually if needed. No tool installation is performed implicitly.

Artifacts:

* `data/<id>/reference/class/classes*.puml`: native class output from external
  tools, or the javac adapter's documented serialization.
* `data/<id>/reference/reference_manifest.json`: exact tool/runtime version,
  argv, shell-quoted command, working directory, exit status, stdout/stderr,
  wall time, source commit/content hash, adapter hashes, class/member/relation
  counts, output hashes, parser counts and failures.
* `data/<id>/reference/reference_facts.json`: independent tool declarations and
  direct inheritance edges. No IR information enters this file.
* `data/<id>/reference/javac_ast.json`: javac's declarations and parse diagnostics.
* `experiments/reverse_engineering/results/extractor_validation.{json,csv}`:
  per-project class, inheritance, attribute and method precision/recall/F1;
  JSON also records every mismatch, ignored pseudo-class kind and collision.

Reruns archive prior class output under `reference/_previous_runs/`; the
production evaluator already skips that directory. Failed reruns leave no
current class diagram or facts. Validation checks source/facts/output hashes
and returns `unavailable` for missing, failed, empty or changed references.

Evaluate the baseline with the existing evaluator, independently of validation:

```sh
APP_PLANTUML_COMMAND=plantuml revision_work/.venv/bin/python -m app.evaluation.evaluate \
  --root data/shopping-cart/reference \
  --ir data/shopping-cart/ir_postprocessed.json \
  --out experiments/reverse_engineering/results/shopping-cart
```

This evaluates against IR as every generated condition does. It does not make
IR into independent ground truth. For class correctness against the reference,
use the independent reference facts (a separate use from baseline-vs-IR scores).

## Toolchains

| Language | Adapter | Version/status in this session |
|---|---|---|
| Python | `pyreverse -o puml -f ALL -m y` | **pylint 3.3.8**, astroid 3.3.11; installed locally from pip cache; real fixture passes |
| Java | OpenJDK compiler parse API, `JavacReference.java` | **OpenJDK 26.0.2.1**, custom adapter; shopping-cart and fixtures pass |
| TypeScript / ES classes in JS | `tplant --project <generated-config> --associations` | **3.1.3 requested**, not installed; TS/JS fixtures explicitly skipped |
| PHP | `ph-puml <snapshot> <output>` | not installed; PHP runtime and Composer also absent |
| Compiler for produced UML | Homebrew `plantuml` | **1.2026.8**, shopping-cart passes `-syntax` |

Python dependencies are exactly pinned in [requirements.txt](requirements.txt).
They were recovered from cached wheel bodies and installed into
`revision_work/.venv`; no global Python packages were changed. Online setup:

```sh
revision_work/.venv/bin/pip install -r experiments/reverse_engineering/requirements.txt
```

For tplant, from this package directory:

```sh
npm install --ignore-scripts --no-audit --no-fund
```

The direct dependency is pinned in `package.json`. Installation failed with
`ENOTFOUND registry.npmjs.org`, so **there is no resolved dependency lock yet**.
Archive the generated `package-lock.json` before reporting any tplant corpus
results. The manifest records the actual installed tplant and Node versions.
The generated config explicitly lists the snapshot files and sets `allowJs`.
Plain JS support is experimental until the real JS fixture passes; prototype
mutation, factory-created objects and runtime metaprogramming are not a
declared-class baseline.

For PHP, `--ph-puml` accepts the script from a local checkout of
[jhofm/ph-puml](https://github.com/jhofm/ph-puml):

```sh
mkdir -p experiments/reverse_engineering/tools
git clone https://github.com/jhofm/ph-puml experiments/reverse_engineering/tools/ph-puml
# Pin a reviewed commit before using this in the study.
# With a compatible PHP runtime and Composer, from that checkout:
composer install --no-dev --no-scripts --no-interaction
```

The runner records the actual tool commit and Composer lock hash/dependency
versions. **This route has not been executed.** Upstream requires PHP ^7.4 and
nikic/php-parser ^4.10; it may be unsuitable for the modern PHP dialect in
laravel-ai. A modern PHP-Parser-based generator may be necessary. Do not call
this a successful PHP baseline without the installation and corpus run.

Java uses `JAVA_HOME/bin/java`, or Homebrew OpenJDK on this Mac, or PATH. Override
with `--java`. It invokes `JavacTask.parse()` only: missing external libraries
are harmless, annotation processors do not run, and target sources are not
compiled or loaded. Named top-level and member types are included; anonymous
and local classes are excluded. Direct fields and explicit methods/constructors
are retained; compiler-generated members are excluded. Internal inheritance
and field-type associations are resolved from lexical scope and imports,
without symbol attribution. Associations are directed field-type references,
not verified ownership/composition. `implements` is rendered as realization;
`extends` as generalization. An established Java RE product still needs to be
run when downloads are available.

## Validation definitions

All matching imports `app.evaluation.evaluate.normalize` directly. Class-like
IR entries include classes/interfaces/enums/traits/records/structs/annotations.
Procedural PHP pseudo-classes (`kind=file`) and functions are counted as ignored,
not as spurious UML classes. Both tools and IR cover the whole prepared snapshot,
including tests; the corpus's vendored-directory exclusions come from `prepare`.

* Class fact: normalized class name.
* Inheritance fact: ordered pair `(child, direct parent)`; includes realization,
  excludes transitive closure and external parents. No inference from endpoints
  alone is allowed. External/unresolved parent counts are disclosed separately.
* Member fact: `(normalized owner, normalized declared member name)`, separately
  for attributes and methods. Constructors count. Overloads collapse to one name.
  Signatures, types, visibility, multiplicity and overload correctness are not
  assessed. Members on IR-only owners remain false positives.
* Precision: intersection / IR fact count. Recall: intersection / reference fact
  count. Undefined denominators are null, never 1.0. Zero matches with nonempty
  sets gives precision=recall=F1=0.
* Simple-name collisions across namespaces/files are reported and excluded from
  every affected class/member/edge score, rather than silently merging them.
  Collision exclusions reduce coverage and must accompany reported metrics.

Tool disagreement is not proof of an extractor defect. Pyreverse's inferred
instance attributes and property handling differ from the current Python
extractor's declared-class attributes and decorated methods. Java records and
enums, PHP traits/promotion, and JS dynamic constructs need separate inspection.
Hand annotation is still required for an expert ground-truth sample.

## Checks and provenance

```sh
revision_work/.venv/bin/python -m pytest -q tests/test_reverse_engineering*.py
```

The tests include real pyreverse and javac runs, unresolved Java dependencies,
deterministic repeated output hashes, syntax failures, stale references, member
ownership, wrong-parent rejection, name collisions and N/A denominators.
Optional tplant integration tests separately exercise `.ts` and plain `.js`.

Primary tool documentation consulted:

* [Pyreverse usage](https://pylint.pycqa.org/en/stable/additional_tools/pyreverse/configuration.html)
* [tplant CLI](https://github.com/bafolts/tplant) and [package metadata](https://raw.githubusercontent.com/bafolts/tplant/master/package.json)
* [ph-puml documentation](https://github.com/jhofm/ph-puml) and [PHP requirements](https://raw.githubusercontent.com/jhofm/ph-puml/master/composer.json)
* [uml-java-doclet's JDK 8 restriction](https://github.com/gboersma/uml-java-doclet)
* [java2plantuml](https://github.com/mirajp1/java2plantuml), source-only candidate not downloaded;
  its [build file](https://raw.githubusercontent.com/mirajp1/java2plantuml/master/build.gradle)
  pins JavaParser 3.4.0, so modern Java coverage needs checking.
