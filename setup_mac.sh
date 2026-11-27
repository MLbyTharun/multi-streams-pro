#!/bin/sh
# Native setup (macOS/Linux). Inference uses the remote vision API.
set -eu
cd "$(dirname "$0")"

PY=""
for candidate in python3.12 python3.11 python3.13 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)'; then
      PY="$candidate"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11+ is required. Install it with: brew install python@3.12" >&2
  exit 1
fi

echo "Using $PY ($($PY -c 'import sys; print(sys.version)'))"
"$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-mac.txt
./.venv/bin/python -m pip freeze > requirements-mac.lock.txt
./.venv/bin/python Check_Environment.py
echo
echo "Native venv is ready. Fill API_BASE_URL, API_KEY and MODEL_NAME in scene_config.py"
echo "(or SCENE_API_* environment variables) before running inference."
