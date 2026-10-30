# IR extraction spot-check — findings (Task #4)

Date: 2026-09-17. Purpose: independent evaluation of IR extraction accuracy (Reviewer 3's
demand). Method: ran the archived extractors (`Archive/app/extractors/` + `postprocess_ir.py`)
via a Python 3.13 venv (`spotcheck/venv`) against freshly cloned copies of four sample
repositories, then compared extracted entities against source ground truth
(AST for Python, declaration regexes for Java/PHP, element inspection for JS).
Repos + commit hashes: `spotcheck/repos_commits.txt`. Scripts: `spotcheck/run_extraction.py`,
`spotcheck/verify_samples.py`. Caveat: clones are at current (Sep 2026) HEAD; the evaluated
snapshot was ~May 2026, so aggregate counts are indicative, not an exact replay. The defect
classes below reproduce independently of repo state (see controlled repro).

## 1. Headline finding — confirmed extraction defect on non-ASCII files

Controlled reproduction (`spotcheck/probe/`):

| Test file | Extracted class name |
|---|---|
| `ascii_test.py` (pure ASCII) | `Alpha` — correct |
| `unicode_test.py` (one comment line with üñî and an em dash) | `':'` and `''` — corrupted |

**Any non-ASCII character in a file corrupts every subsequent entity name in that file.**
Cause: the extractors parse `bytes(code,"utf-8")` but slice the decoded `str` with the
resulting byte offsets (`_get_code_segment` in every extractor); multi-byte characters
desynchronize byte and character indices. Names become truncated fragments; some become
empty and are then dropped by post-processing (entities silently disappear). This is
trivial to fix (extract `node.text` from the byte string, or slice encoded bytes) but it
means the archived IRs are corrupted for any file containing accented characters, em-dashes,
arrows, or emoji — i.e., a large fraction of real-world files.

## 2. Per-language sample results

**Java (shopping-cart, ASCII-only — control case):**
- 30/30 files with `class` declarations extracted; `ProductBean.java`: 14/14 methods,
  7/7 fields — extraction is pixel-perfect on ASCII source.
- 7 files declare interfaces; none are extracted (extractor only handles `class_declaration`).
  Interfaces, enums and annotations are silently absent from the IR — for UML class
  diagrams this is a systematic recall gap.

**Python (GenericAgent):**
- Sample file `desktop_bridge.py`: 102 ground-truth top-level entities; IR contains 99
  elements of which ~6 are garbage fragments (`'sages:'`, `'self.running_se'`, `'": False, "error"'`).
  Classes such as `Session` (a `@dataclass`), `AgentManager`, `WsHub` appear only as
  mangled names or vanish (empty-name filtering). File contains non-ASCII → consistent
  with the offset defect; decorated classes add a second failure path.
- `tuiapp_v2.py`: 91 GT vs 98 IR elements, 6 classes missing, similar garbage.

**JavaScript (impeccable):**
- 26,709 raw elements from 294 files; **44.6% (11,903) have non-identifier names**
  (e.g., `'g.disabledRules)'`, `'{ el, findings'`) — the offset defect plus arrow-function
  over-capture (10,237 `arrow_function` elements).
- Content is duplicated in eight mirrored tool-config directories
  (`.hermes/.kiro/.vibe/.agent/.pi/.cursor-plugin/.github/...`, 1,183 elements each) —
  the extractor ingests every copy. No source-dir scoping.

**PHP (PHPMailer):**
- Class-level recall good: 58 classes + 1 interface from `src/`+`examples/`+`test/`
  vs 60 `class` declarations. `PHPMailer.php`: 129 methods extracted (count matches), but
  10+ names are mangled (`'$na'`, `'($ce'`, `'ustomHeader($na'`) and all five `DKIM_*`
  methods are missing — file contains non-ASCII.
- Properties: 79/79 with visibility/static metadata — clean.

## 3. Post-processing issues

- **Function double-counting**: functions are appended to `classes` *and* `functions`
  (Python 1,567 raw → 2,747 counted; JS 26,709 → 30,408; PHP 133 + 2 duplicated).
  The metric's denominator (`total_ir_elements`) thus depends on a definition quirk.
- **Inconsistent filtering across sizes**: small projects (≤100/200 elements) get
  "minimal filtering", large projects run `is_anonymous_or_trivial` — which keeps
  garbage fragments (they have "content") but drops empty-named entities. Result:
  the same file extracts differently depending on repository size.

## 4. Cross-generation count divergence (same repository)

| Project | Paper Table 5 | Released metrics.json | Rerun (archived code, Sep HEAD) |
|---|---|---|---|
| shopping-cart | 31 | 30 | 30 |
| GenericAgent | 155 | 466 | 2,747 counted (1,567 raw) |
| impeccable | 200 | 787 | 30,408 counted (26,709 raw) |
| PHPMailer | 84 | 81,535 | 135 |

Four irreconcilable generations of the same quantity. The 81,535 for PHPMailer cannot be
produced by any inspected code path and remains unexplained (flagged in RESULTS_SUMMARY §3.5).
Conclusion: IR counts must be regenerated for v2 by one documented, released extractor on
pinned commits — otherwise no denominator in the paper is defensible.

## 5. Implications for v2 (R3's IR criticism)

1. Fix the byte/character offset defect (use `node.text`); drop or repair the latin-1
   fallback path.
2. Scope extraction: include only source directories; exclude mirror/config dirs,
   `node_modules`, `dist`, `build`, `vendor`; deduplicate mirrored content.
3. Define inclusion rules (interfaces, enums, decorated classes, arrow functions) and
   document them; remove the class/function duplication in post-processing.
4. Re-run extraction on pinned commits for all 12 repositories (no LLM cost), regenerate
   IR views + denominators, and update every metric.
5. Report the extraction-accuracy sample (as here, but on a pre-labeled sample with the
   fixed extractor) as a paper subsection — this directly answers R3 and strengthens the
   "what is excluded" discussion for R2.

Status: spot-check complete; extractor fix + re-extraction is the recommended next build step.
