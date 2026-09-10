#!/usr/bin/env bash
# Local CI-equivalent. Every step runs independently and never short-circuits,
# so one invocation reports every gate's real outcome rather than only the first
# failure. Mirrors .github/workflows/ci.yml step for step.
set -u

if [ -x ".venv/Scripts/python.exe" ]; then PY=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ];       then PY=".venv/bin/python"
else PY="python"; fi

declare -A OUTCOMES
run_step() {
  local name="$1"; shift
  echo "== $name =="
  if "$@"; then OUTCOMES[$name]="success"; else OUTCOMES[$name]="failure"; fi
}

run_step lint      "$PY" -m ruff check .
run_step format    "$PY" -m ruff format --check .
run_step typecheck "$PY" -m mypy ops
run_step pytest    "$PY" -m pytest -q
run_step gatectl   "$PY" ops/gatectl.py validate

echo
echo "== summary =="
overall=0
for name in lint format typecheck pytest gatectl; do
  echo "$name: ${OUTCOMES[$name]}"
  [ "${OUTCOMES[$name]}" = "success" ] || overall=1
done
exit "$overall"
