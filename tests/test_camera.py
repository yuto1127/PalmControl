from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2

# 研究用途の単体スクリプトは「そのまま python tests/test_xxx.py」で動かせることが重要。
# しかしスクリプト実行ではプロジェクトルートがsys.pathに入らず、`src`をimportできない場合がある。
# ここでは最小限の手当として、プロジェクトルートを探索パスへ追加する。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config_loader import get_config_store


def main() -> int:
    """カメラ取得の最小動作確認スクリプト。

    目的（spec 5.2）:
    - `config/settings.yaml` の解像度/FPS設定が読み込めること
    - OpenCVでカメラが開けること
    - プレビューが表示でき、実測FPSを確認できること

    注意:
    - macOSでは初回実行時にカメラ権限の許可が必要になる場合がある。
      権限不足の場合、OpenCVの `VideoCapture` が開けない、またはフレームが取得できないことがある。
    """

    store = get_config_store("config/settings.yaml")
    cfg = store.get().camera

    cap = cv2.VideoCapture(cfg.device_id)
    if not cap.isOpened():
        print(
            "カメラを開けませんでした。\n"
            "- device_idの確認\n"
            "- macOSのカメラ権限（プライバシーとセキュリティ）\n"
            "- 他アプリがカメラを占有していないか\n"
        )
        return 1

    # 要求値を設定（デバイスやドライバによっては反映されないことがある）
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(cfg.width))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(cfg.height))
    cap.set(cv2.CAP_PROP_FPS, float(cfg.fps))

    prev_t = time.perf_counter()
    fps = 0.0

    print("プレビュー開始: 終了は 'q' キー")
    while True:
        ok, frame = cap.read()
        frame = cv2.flip(frame, 1)
        if not ok or frame is None:
            print("フレームを取得できませんでした。権限や接続を確認してください。")
            break

        now = time.perf_counter()
        dt = now - prev_t
        prev_t = now
        if dt > 0:
            # 表示が読みやすいように軽く平滑化する
            inst = 1.0 / dt
            fps = (fps * 0.9) + (inst * 0.1) if fps > 0 else inst

        h, w = frame.shape[:2]
        text = f"{w}x{h}  fps={fps:.1f}  (req {cfg.fps})"
        cv2.putText(
            frame,
            text,
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

        cv2.imshow("PalmControl - Camera Test", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

