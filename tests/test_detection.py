from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.request import urlretrieve

import cv2

# 単体スクリプトとして直接実行できるように、プロジェクトルートを探索パスへ追加する。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# mediapipeは内部でmatplotlib等を読み込むことがあり、初回にキャッシュ書き込みが走る。
# 研究用スクリプトとしてどこでも動かせるよう、書き込み可能な場所へ誘導する。
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / "logs" / "mplconfig"))
os.environ.setdefault("MEDIAPIPE_DISABLE_GPU", "1")

import mediapipe as mp  # noqa: E402

from src.core.detector import HandDetector  # noqa: E402
from src.utils.camera import open_camera  # noqa: E402
from src.utils.config_loader import get_config_store  # noqa: E402
from src.utils.logger import LoggingManager  # noqa: E402


MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
MODEL_PATH = PROJECT_ROOT / "models" / "hand_landmarker.task"


HAND_CONNECTIONS = (
    # Thumb
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 4),
    # Index
    (0, 5),
    (5, 6),
    (6, 7),
    (7, 8),
    # Middle
    (0, 9),
    (9, 10),
    (10, 11),
    (11, 12),
    # Ring
    (0, 13),
    (13, 14),
    (14, 15),
    (15, 16),
    # Pinky
    (0, 17),
    (17, 18),
    (18, 19),
    (19, 20),
    # Palm
    (5, 9),
    (9, 13),
    (13, 17),
)


def ensure_model_file() -> None:
    """HandLandmarkerモデルをローカルに用意する。

    テスト用途では「最小手数で動かせること」を優先し、無ければダウンロードする。
    本体実装ではモデルのバージョン管理方法（同梱/固定URL/手動配置）を改めて設計する。
    """

    if MODEL_PATH.exists():
        return
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"モデル未配置のためダウンロードします: {MODEL_URL}")
    urlretrieve(MODEL_URL, MODEL_PATH)  # nosec - research/test utility
    print(f"保存しました: {MODEL_PATH}")


def draw_landmarks_bgr(frame_bgr, hand_landmarks) -> None:
    """ランドマークをフレーム上に描画する（Tasks APIの出力に対応）。"""

    h, w = frame_bgr.shape[:2]
    pts = []
    for lm in hand_landmarks:
        x_px = int(lm.x * w)
        y_px = int(lm.y * h)
        pts.append((x_px, y_px))
        cv2.circle(frame_bgr, (x_px, y_px), 3, (0, 255, 0), -1)

    for a, b in HAND_CONNECTIONS:
        if 0 <= a < len(pts) and 0 <= b < len(pts):
            cv2.line(frame_bgr, pts[a], pts[b], (0, 200, 255), 2)


def main() -> int:
    """MediaPipeハンドトラッキングの最小動作確認スクリプト。

    目的（spec 5.2 / 次タスク要件）:
    - カメラ映像を取得し、MediaPipeの骨格（Landmarks）をオーバーレイ表示できること
    - 現在検知しているモード（Mouse / Scroll / None）を画面に表示できること
    - 検出処理時間（ms）を可視化し、loggerへも出力できること
    """

    store = get_config_store("config/settings.yaml")
    settings = store.get()

    ensure_model_file()

    log_manager = LoggingManager(store)
    detector = HandDetector(
        store,
        logger=log_manager.logger,
        mirror_x=True,
        max_num_hands=2,
        model_path=MODEL_PATH,
    )

    opened = open_camera(settings.camera.device_id)
    cap = opened.cap
    if cap is None or (not cap.isOpened()):
        tried = ", ".join(opened.tried_backends) if opened.tried_backends else "default"
        print(f"カメラを開けませんでした。device_idや権限を確認してください。試行: {tried}")
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(settings.camera.width))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(settings.camera.height))
    cap.set(cv2.CAP_PROP_FPS, float(settings.camera.fps))

    prev_t = time.perf_counter()
    fps = 0.0

    print("プレビュー開始: 終了は 'q' キー")
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            print("フレームを取得できませんでした。権限や接続を確認してください。")
            break

        # 解析
        res = detector.process(frame)

        # FPS（表示用）
        now = time.perf_counter()
        dt = now - prev_t
        prev_t = now
        if dt > 0:
            inst = 1.0 / dt
            fps = (fps * 0.9) + (inst * 0.1) if fps > 0 else inst

        # ランドマーク描画
        if res.hand_landmarks is not None:
            draw_landmarks_bgr(frame, res.hand_landmarks)

        # 画面表示（モード/指本数/コンタクト/遅延）
        lines = [
            f"mode={res.mode}",
            f"fingers={res.finger_count}",
            f"contact={res.contact}",
            f"contact_dist={res.contact_distance:.4f}" if res.contact_distance is not None else "contact_dist=None",
            f"latency_ms={res.latency_ms:.1f}",
            f"fps={fps:.1f}",
        ]
        y0 = 25
        for i, txt in enumerate(lines):
            cv2.putText(
                frame,
                txt,
                (10, y0 + i * 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

        cv2.imshow("PalmControl - Detection Test", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break

    detector.close()
    log_manager.close()
    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

