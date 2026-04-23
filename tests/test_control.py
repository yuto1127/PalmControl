from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import cv2
from pynput import keyboard

# 単体スクリプトとして直接実行できるように、プロジェクトルートを探索パスへ追加する。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# キャッシュ書き込み先（環境差の吸収）
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / "logs" / "mplconfig"))
os.environ.setdefault("MEDIAPIPE_DISABLE_GPU", "1")

from src.core.controller import MouseController  # noqa: E402
from src.core.detector import HandDetector  # noqa: E402
from src.utils.camera import open_camera  # noqa: E402
from src.utils.config_loader import get_config_store  # noqa: E402
from src.utils.logger import LoggingManager  # noqa: E402


def main() -> int:
    """detector + controller の統合動作確認。

    研究用途の意図:
    - 検出（座標/モード/contact）→制御（移動/クリック/ドラッグ/スクロール）の接続を、最短で検証する。
    - 実機操作を伴うため、必ず安全停止（Esc）で即座に制御を解除できるようにする（spec 7.139）。
    """

    store = get_config_store("config/settings.yaml")
    settings = store.get()

    log_manager = LoggingManager(store)
    detector = HandDetector(store, logger=log_manager.logger, mirror_x=True, max_num_hands=2)
    controller = MouseController(store, logger=log_manager.logger)

    stop_event = threading.Event()

    def on_press(key):
        if key == keyboard.Key.esc:
            stop_event.set()
            return False  # listener stop
        return True

    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    opened = open_camera(settings.camera.device_id)
    cap = opened.cap
    if cap is None or (not cap.isOpened()):
        tried = ", ".join(opened.tried_backends) if opened.tried_backends else "default"
        print(f"カメラを開けませんでした。device_idや権限を確認してください。試行: {tried}")
        stop_event.set()

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(settings.camera.width))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(settings.camera.height))
    cap.set(cv2.CAP_PROP_FPS, float(settings.camera.fps))

    prev_t = time.perf_counter()
    fps = 0.0

    print("統合テスト開始: Escで安全停止、qでウィンドウ終了")
    while not stop_event.is_set():
        ok, frame = cap.read()
        if not ok or frame is None:
            print("フレームを取得できませんでした。権限や接続を確認してください。")
            break

        det = detector.process(frame)

        # controllerは内部でmodeに応じて「移動/スクロール」を分岐する。
        # クリック判定（contactキュー）は mode=None でも走らせたいので、常にupdateへ渡す。
        out = controller.update(det)

        # FPS（表示用）
        now = time.perf_counter()
        dt = now - prev_t
        prev_t = now
        if dt > 0:
            inst = 1.0 / dt
            fps = (fps * 0.9) + (inst * 0.1) if fps > 0 else inst

        # 表示（手は描画しない。制御が入っているかの簡易確認に留める）
        dbg = controller.get_debug_state()
        dxdy = ""
        if det.pointer_xy is not None and dbg.get("prev_hand_xy") is not None:
            px, py = dbg["prev_hand_xy"]
            dxdy = f"dxdy=({det.pointer_xy[0]-px:+.3f},{det.pointer_xy[1]-py:+.3f})"

        lines = [
            f"mode={det.mode}",
            f"index_ext={getattr(det,'index_extended',None)} middle_ext={getattr(det,'middle_extended',None)}",
            f"contact={det.contact}",
            f"contact_dist={det.contact_distance:.4f}" if det.contact_distance is not None else "contact_dist=None",
            f"pointer_xy={det.pointer_xy[0]:.3f},{det.pointer_xy[1]:.3f}" if det.pointer_xy is not None else "pointer_xy=None",
            f"prev_hand={dbg.get('prev_hand_xy')}",
            dxdy if dxdy else "dxdy=n/a",
            f"streak={dbg.get('mouse_mode_streak')}/{store.get().control.mouse_mode_stable_frames} click_pose={dbg.get('click_pose_active')}",
            f"latency_ms={det.latency_ms:.1f}",
            f"fps={fps:.1f}",
            "STOP: Esc",
        ]
        if out is not None:
            lines.append(f"anchored={out.anchored}")
            if out.left_clicked:
                lines.append("left_click")
            if out.right_clicked:
                lines.append("right_click")
            if out.drag_down:
                lines.append("drag_down")
            if out.drag_up:
                lines.append("drag_up")
            if out.scrolled:
                lines.append("scroll")

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

        cv2.imshow("PalmControl - Control Test", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break

    # 安全停止：押下状態を必ず解除
    controller.reset()
    detector.close()
    log_manager.close()
    try:
        cap.release()
    except Exception:
        pass
    cv2.destroyAllWindows()
    stop_event.set()
    try:
        listener.stop()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

