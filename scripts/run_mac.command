#!/bin/bash
set -euo pipefail

# PalmControl launcher (macOS)
# - 初回: venv作成 → pip更新 → 依存導入
# - 2回目以降: venvを再利用して起動

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3 が見つかりません。Python 3 をインストールしてください。"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[PalmControl] 初回セットアップ: .venv を作成します"
  "$PYTHON_BIN" -m venv .venv
fi

echo "[PalmControl] 依存関係を更新します"
. ".venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "[PalmControl] アプリを起動します"
python -m src.main

