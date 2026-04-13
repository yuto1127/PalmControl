#!/bin/bash
set -euo pipefail

case "${1:-}" in
  camera)   target="tests/test_camera.py" ;;
  detection) target="tests/test_detection.py" ;;
  control)  target="tests/test_control.py" ;;
  ""|-h|--help)
    echo "Usage: ./tests/run_test.sh {camera|detection|control}"
    exit 0
    ;;
  *)
    echo "Unknown target: $1"
    echo "Usage: ./tests/run_test.sh {camera|detection|control}"
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

exec "$PY" "$target"

