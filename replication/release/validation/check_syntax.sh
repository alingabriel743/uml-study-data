#!/bin/bash
# Independent PlantUML syntax validation of the Code2UML corpus (v2 rebuild).
# Runs `plantuml --check-syntax` on every .puml file and records the exit code.
#   0   = accepted
#   200 = syntax error(s)
#   100 = no diagram found (when applicable)
#
# Usage:   ./check_syntax.sh [corpus-root] [output-tsv]
# Default: corpus = ~/Desktop/UML upload/uml-study-data-main
#          output = v2-rebuild/results/syntax_check.tsv
set -u
ROOT="${1:-$HOME/Desktop/UML upload/uml-study-data-main}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="${2:-$SCRIPT_DIR/../results/syntax_check.tsv}"
LOGDIR="$SCRIPT_DIR/../logs"
mkdir -p "$(dirname "$OUT")" "$LOGDIR"

plantuml --version > "$LOGDIR/plantuml_version.txt" 2>&1
printf 'file\texit_code\n' > "$OUT"
export OUT

find "$ROOT" -name '*.puml' -print0 | xargs -0 -P 4 -I {} bash -c '
  f="$1"
  plantuml --check-syntax "$f" >/dev/null 2>&1
  printf "%s\t%s\n" "$f" "$?" >> "$OUT"
' _ {}

echo "checked $(($(wc -l < "$OUT") - 1)) files -> $OUT"
