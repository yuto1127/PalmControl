from __future__ import annotations

import os
import time
from typing import Optional, Tuple

import cv2
from PyQt6.QtCore import QMutex, QObject, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage

from src.core.controller import MouseController
from src.core.detector import HandDetector
from src.utils.config_loader import ConfigStore, Settings
from src.utils.logger import LoggingManager


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


def _to_qimage(frame_bgr) -> QImage:
    """OpenCV(BGR)画像をQImageへ変換する。

    重要:
    - QImageは内部バッファを参照するため、ここでは `.copy()` して寿命問題を回避する。
    """

    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    bytes_per_line = 3 * w
    qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
    return qimg.copy()


def _draw_landmarks_bgr(frame_bgr, hand_landmarks) -> None:
    """Tasks APIのランドマーク（list）をフレームへ描画する。"""

    if hand_landmarks is None:
        return
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


class VisionControlWorker(QThread):
    """カメラ→検出→（任意で制御）を回すバックグラウンドスレッド。

    目的:
    - GUIスレッドをブロックせずにリアルタイム処理を回す。
    - プレビュー（QImage生成・転送）は表示時だけ行い、非表示時は負荷を最小化する。
    - OS操作はデフォルトOFF（安全）。明示的にONにした場合のみControllerを動かす。
    """

    frameReady = pyqtSignal(QImage)
    statusReady = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, store: ConfigStore, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._store = store

        self._mutex = QMutex()
        self._stop = False
        self._preview_enabled = False
        self._control_enabled = False
        self._restart_camera = False

        # mediapipeがGPU初期化を試みる環境差対策
        os.environ.setdefault("MEDIAPIPE_DISABLE_GPU", "1")

        self._log_manager: Optional[LoggingManager] = None
        self._detector: Optional[HandDetector] = None
        self._controller: Optional[MouseController] = None
        self._cap: Optional[cv2.VideoCapture] = None

    @pyqtSlot(bool)
    def setPreviewEnabled(self, enabled: bool) -> None:
        with QMutexLocker(self._mutex):
            self._preview_enabled = bool(enabled)

    @pyqtSlot(bool)
    def setControlEnabled(self, enabled: bool) -> None:
        with QMutexLocker(self._mutex):
            self._control_enabled = bool(enabled)

    @pyqtSlot()
    def requestRestartCamera(self) -> None:
        with QMutexLocker(self._mutex):
            self._restart_camera = True

    def stop(self) -> None:
        with QMutexLocker(self._mutex):
            self._stop = True

    def run(self) -> None:
        """スレッド本体。"""

        try:
            self._log_manager = LoggingManager(self._store)
            self._detector = HandDetector(self._store, logger=self._log_manager.logger, mirror_x=True, max_num_hands=2)
            self._controller = MouseController(self._store, logger=self._log_manager.logger)

            self._open_camera(self._store.get())

            prev_t = time.perf_counter()
            fps = 0.0
            while True:
                preview_enabled, control_enabled, restart_camera, should_stop = self._snapshot_flags()
                if should_stop:
                    break
                if restart_camera:
                    self._close_camera()
                    self._open_camera(self._store.get())
                    with QMutexLocker(self._mutex):
                        self._restart_camera = False

                if self._cap is None:
                    time.sleep(0.05)
                    continue

                ok, frame = self._cap.read()
                if not ok or frame is None:
                    self.error.emit("フレームを取得できませんでした（カメラ権限/接続を確認）。")
                    time.sleep(0.2)
                    continue

                det = self._detector.process(frame) if self._detector is not None else None

                ctrl_out = None
                # Controllerは常に状態更新（dry-run可）し、GUIのデバッグ表示に使う。
                # OSへの実操作（pyautogui）は control_enabled=True のときのみ。
                if det is not None and self._controller is not None:
                    try:
                        ctrl_out = self._controller.update(det, apply_actions=bool(control_enabled))
                    except Exception:
                        ctrl_out = None

                # FPS計測（表示用）
                now = time.perf_counter()
                dt = now - prev_t
                prev_t = now
                if dt > 0:
                    inst = 1.0 / dt
                    fps = (fps * 0.9) + (inst * 0.1) if fps > 0 else inst

                if det is not None:
                    settings = self._store.get()
                    anch = settings.control.cursor_anchoring
                    pre_contact = False
                    if det.contact_distance is not None:
                        pre_contact = bool(float(det.contact_distance) <= float(anch.pre_contact_threshold))

                    dbg = {}
                    try:
                        if self._controller is not None:
                            dbg = self._controller.get_debug_state()
                    except Exception:
                        dbg = {}

                    ctrl = {}
                    if ctrl_out is not None:
                        try:
                            ctrl = ctrl_out.__dict__.copy()
                        except Exception:
                            ctrl = {}

                    self.statusReady.emit(
                        {
                            "mode": det.mode,
                            "finger_count": int(det.finger_count),
                            "contact": bool(det.contact),
                            "contact_distance": det.contact_distance,
                            "pre_contact": bool(pre_contact),
                            "latency_ms": det.latency_ms,
                            "fps": fps,
                            "index_extended": bool(det.index_extended),
                            "middle_extended": bool(det.middle_extended),
                            "control_enabled": bool(control_enabled),
                            "control": ctrl,
                            "controller": dbg,
                        }
                    )

                # プレビューは有効時のみ（負荷対策）
                if preview_enabled and det is not None:
                    view = frame.copy()
                    _draw_landmarks_bgr(view, det.hand_landmarks)
                    # 簡易オーバーレイ
                    settings = self._store.get()
                    anch = settings.control.cursor_anchoring
                    pre_contact = False
                    if det.contact_distance is not None:
                        pre_contact = bool(float(det.contact_distance) <= float(anch.pre_contact_threshold))

                    dbg = {}
                    try:
                        if self._controller is not None:
                            dbg = self._controller.get_debug_state()
                    except Exception:
                        dbg = {}

                    line1 = f"mode={det.mode}  fps={fps:.1f}  lat={det.latency_ms:.1f}ms"
                    line2 = (
                        f"c={int(bool(det.contact))} pre={int(bool(pre_contact))} "
                        f"pose={int(bool(dbg.get('click_pose_active')))} "
                        f"drag={int(bool(dbg.get('dragging')))} "
                        f"freeze={int(bool(dbg.get('anchoring_freeze')))} "
                        f"os={int(bool(control_enabled))}"
                    )
                    cv2.putText(view, line1, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2, cv2.LINE_AA)
                    cv2.putText(view, line2, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2, cv2.LINE_AA)
                    self.frameReady.emit(_to_qimage(view))
                else:
                    # プレビューOFF時はQImage生成しない（CPU節約）
                    pass

        except Exception as e:
            self.error.emit(str(e))
        finally:
            try:
                if self._controller is not None:
                    self._controller.reset()
            except Exception:
                pass
            try:
                if self._detector is not None:
                    self._detector.close()
            except Exception:
                pass
            try:
                if self._log_manager is not None:
                    self._log_manager.close()
            except Exception:
                pass
            self._close_camera()

    def _snapshot_flags(self) -> Tuple[bool, bool, bool, bool]:
        with QMutexLocker(self._mutex):
            return (
                self._preview_enabled,
                self._control_enabled,
                self._restart_camera,
                self._stop,
            )

    def _open_camera(self, settings: Settings) -> None:
        self._close_camera()
        cap = cv2.VideoCapture(settings.camera.device_id)
        if not cap.isOpened():
            self._cap = None
            self.error.emit("カメラを開けませんでした（device_id/権限/占有を確認）。")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(settings.camera.width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(settings.camera.height))
        cap.set(cv2.CAP_PROP_FPS, float(settings.camera.fps))
        self._cap = cap

    def _close_camera(self) -> None:
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = None


class QMutexLocker:
    """PyQtのQMutexをwithで扱うための小さなヘルパー。"""

    def __init__(self, mutex: QMutex) -> None:
        self._mutex = mutex

    def __enter__(self):
        self._mutex.lock()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._mutex.unlock()
        return False

