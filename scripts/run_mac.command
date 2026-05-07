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
# クラッシュ切り分け用:
# - macOS 全画面オーバーレイ補助: safe / aggressive / off
#   safe: collectionBehaviorのみ（比較的安全）
#   aggressive: 最前面レベル/順序まで強制（環境によって不安定）
export PALMCONTROL_MACOS_OVERLAY="${PALMCONTROL_MACOS_OVERLAY:-safe}"
# 全画面動画の上に Qt が載らない環境向け: NSPanel 上に円形 PieMenu を描画する（PyObjC 必須）
export PALMCONTROL_PANEL_OVERLAY="${PALMCONTROL_PANEL_OVERLAY:-1}"
# PieMenu 表示時に OS カーソルをメニュー中央へ移すのを止める場合: export PALMCONTROL_PIE_WARP_CURSOR=0
# PieMenu の向き補正（上下反転などで迷子になりやすいので、まずは既定=補正なしで固定）
# 変更したい場合はこの行を編集してください（例: export PALMCONTROL_PIE_INVERT_Y=1）
export PALMCONTROL_PIE_INVERT_Y=0
# pointer_xy のY軸（通常は画像座標系で「下が+」）
export PALMCONTROL_PIE_Y_AXIS=down
# PieMenu 確定操作（single=単発ピンチ / double=ダブルピンチ）
export PALMCONTROL_PIE_CLICK_MODE="${PALMCONTROL_PIE_CLICK_MODE:-double}"
export PALMCONTROL_PIE_CLICK_COOLDOWN_MS="${PALMCONTROL_PIE_CLICK_COOLDOWN_MS:-220}"
# ログが多すぎる場合は既定でOFF（必要なときだけ export PALMCONTROL_OVERLAY_DEBUG=1）
export PALMCONTROL_OVERLAY_DEBUG="${PALMCONTROL_OVERLAY_DEBUG:-0}"
# PieMenu / worker.pie の大量ログ（既定OFF。必要なときだけ export PALMCONTROL_PIE_DEBUG=1）
export PALMCONTROL_PIE_DEBUG="${PALMCONTROL_PIE_DEBUG:-0}"
# macos_overlay のログ（既定OFF。必要なときだけ export PALMCONTROL_MACOS_OVERLAY_DEBUG=1）
export PALMCONTROL_MACOS_OVERLAY_DEBUG="${PALMCONTROL_MACOS_OVERLAY_DEBUG:-0}"
export PYTHONFAULTHANDLER=1
python -X faulthandler -m src.main

