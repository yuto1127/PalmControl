from __future__ import annotations

import os
import time
from typing import Optional, Tuple

import cv2
from PyQt6.QtCore import QMutex, QObject, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage

from src.core.controller import MouseController
from src.core.detector import DualHandResult, HandDetector
from src.utils.camera import open_camera
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
    pieMenuStateReady = pyqtSignal(dict)
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
        self._last_capture_error_ms: int = 0

        # PieMenu連携用（GUI側でオーバーレイ表示を行う）
        self._pie_active: bool = False
        self._pie_scroll_prev_center_y: Optional[float] = None
        self._pie_scroll_last_step_ms: int = 0
        self._pie_open_streak: int = 0
        self._pie_close_streak: int = 0
        self._pie_pointer_contact_prev: bool = False

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
                    now_ms = int(time.time() * 1000)
                    # 取得失敗時のログはスパム回避のため2秒間隔で出す。
                    if (now_ms - self._last_capture_error_ms) >= 2000:
                        self._last_capture_error_ms = now_ms
                        self._log_event(
                            "WARN",
                            "camera.read_failed",
                            {"reason": "cap.read returned empty frame"},
                        )
                    self.error.emit("フレームを取得できませんでした（カメラ権限/接続を確認）。")
                    time.sleep(0.2)
                    continue

                dual = self._detector.process(frame) if self._detector is not None else None

                pointer_det = None
                command_det = None
                settings = self._store.get()
                if dual is not None:
                    pointer_det, command_det = self._assign_roles(dual, settings)

                # PieMenuは「危険なOS操作」を明示的にONにしたときだけ出す（安全側の挙動）
                # PieMenu表示条件（非利き手）:
                # - 親指だけ => Preset 1
                # - 人差し指だけ => Preset 2
                # - 親指+人差し指 => Preset 3
                command_open = False
                command_preset = 0
                if command_det is not None:
                    thumb = bool(getattr(command_det, "thumb_extended", False))
                    index = bool(getattr(command_det, "index_extended", False))
                    middle = bool(getattr(command_det, "middle_extended", False))
                    # ring/pinky は DetectionResult に無いので、finger_count を利用して「単指」を判定する
                    fc = int(getattr(command_det, "finger_count", 0))

                    # Preset 1: グー（指が1本も伸びていない）
                    if fc == 0:
                        command_open = True
                        command_preset = 1
                    # Preset 2: 人差し指だけ
                    elif index and (not thumb) and (not middle) and fc == 1:
                        command_open = True
                        command_preset = 2
                    # Preset 3: 親指 + 人差し指
                    elif thumb and index and fc == 2:
                        command_open = True
                        command_preset = 3

                # 手の検出は瞬断することがあるため、表示条件はラッチしてチラつきを抑える。
                if command_open:
                    self._pie_open_streak += 1
                    self._pie_close_streak = 0
                else:
                    self._pie_close_streak += 1
                    self._pie_open_streak = 0

                open_need = 2  # 連続2フレームで「開く」を確定
                # 閉じる遅延が長すぎると「手をどかしてもしばらく残る」体感になるため短めにする。
                # 目標: チラつきは抑えつつ、手を外したらすぐ消える。
                close_need = 6  # 連続6フレームで「閉じる」を確定

                if not control_enabled:
                    pie_should_active = False
                else:
                    if not self._pie_active:
                        pie_should_active = bool(command_open and self._pie_open_streak >= open_need)
                    else:
                        pie_should_active = bool(command_open or (self._pie_close_streak < close_need))

                if pie_should_active != self._pie_active:
                    self._pie_active = bool(pie_should_active)
                    # 表示開始時はスクロール基準をリセット（プリセット切替の原点）
                    self._pie_scroll_prev_center_y = None
                    self._pie_scroll_last_step_ms = 0

                ctrl_out = None
                # Controllerは常に状態更新（dry-run可）し、GUIのデバッグ表示に使う。
                # OSへの実操作（pyautogui）は control_enabled=True のときのみ。
                if pointer_det is not None and self._controller is not None:
                    try:
                        # PieMenu表示中はカーソルの「仮想中心固定」を優先するため、OS操作は抑止する。
                        # 実行はPieMenu側（コマンド実行層）で行う。
                        apply = bool(control_enabled) and (not self._pie_active)
                        ctrl_out = self._controller.update(pointer_det, apply_actions=apply)
                    except Exception:
                        ctrl_out = None

                # FPS計測（表示用）
                now = time.perf_counter()
                dt = now - prev_t
                prev_t = now
                if dt > 0:
                    inst = 1.0 / dt
                    fps = (fps * 0.9) + (inst * 0.1) if fps > 0 else inst

                if dual is not None:
                    anch = settings.control.cursor_anchoring
                    pre_contact = False
                    if pointer_det is not None and pointer_det.contact_distance is not None:
                        pre_contact = bool(float(pointer_det.contact_distance) <= float(anch.pre_contact_threshold))

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

                    # PieMenuの確定（クリック）:
                    # クリック系のタップ判定は「操作のための姿勢変化」で取りこぼしやすいので、
                    # PieMenu表示中は contact ではなく「距離(contact_distance)」で確定し、反応を軽くする。
                    pie_click = False
                    if self._pie_active and pointer_det is not None:
                        d = getattr(pointer_det, "contact_distance", None)
                        th = float(getattr(settings.pie_menu, "click_threshold", 0.085))
                        cur = bool(d is not None and float(d) <= th)
                        pie_click = bool(cur and (not self._pie_pointer_contact_prev))
                        self._pie_pointer_contact_prev = bool(cur)
                    else:
                        self._pie_pointer_contact_prev = False

                    # プリセット切替は「中央クリック」に寄せる（スクロール切替は無効化）
                    preset_step = 0

                    self.pieMenuStateReady.emit(
                        {
                            "active": bool(self._pie_active),
                            "pointer": {
                                "handedness": getattr(pointer_det, "handedness", None) if pointer_det else None,
                                "mode": getattr(pointer_det, "mode", "None") if pointer_det else "None",
                                "pointer_xy": getattr(pointer_det, "pointer_xy", None) if pointer_det else None,
                                "contact": bool(getattr(pointer_det, "contact", False)) if pointer_det else False,
                                "left_clicked": bool(pie_click) if self._pie_active else (bool(ctrl.get("left_clicked")) if ctrl else False),
                                "right_clicked": False,
                            },
                            "command": {
                                "handedness": getattr(command_det, "handedness", None) if command_det else None,
                                "mode": getattr(command_det, "mode", "None") if command_det else "None",
                                "open": bool(command_open),
                                "preset": int(command_preset),
                            },
                            "preset_step": int(preset_step),
                        }
                    )

                    self.statusReady.emit(
                        {
                            "pie_active": bool(self._pie_active),
                            "pointer_handedness": getattr(pointer_det, "handedness", None) if pointer_det else None,
                            "command_handedness": getattr(command_det, "handedness", None) if command_det else None,
                            "mode": getattr(pointer_det, "mode", "None") if pointer_det else "None",
                            "finger_count": int(getattr(pointer_det, "finger_count", 0)) if pointer_det else 0,
                            "contact": bool(getattr(pointer_det, "contact", False)) if pointer_det else False,
                            "contact_distance": getattr(pointer_det, "contact_distance", None) if pointer_det else None,
                            "pre_contact": bool(pre_contact),
                            "latency_ms": float(getattr(pointer_det, "latency_ms", 0.0)) if pointer_det else 0.0,
                            "fps": fps,
                            "index_extended": bool(getattr(pointer_det, "index_extended", False)) if pointer_det else False,
                            "middle_extended": bool(getattr(pointer_det, "middle_extended", False)) if pointer_det else False,
                            "control_enabled": bool(control_enabled),
                            "control": ctrl,
                            "controller": dbg,
                        }
                    )

                # プレビューは有効時のみ（負荷対策）
                if preview_enabled and dual is not None:
                    view = frame.copy()
                    # 両手を描画（存在するものだけ）
                    if dual.left is not None:
                        _draw_landmarks_bgr(view, dual.left.hand_landmarks)
                    if dual.right is not None:
                        _draw_landmarks_bgr(view, dual.right.hand_landmarks)
                    # 簡易オーバーレイ
                    anch = settings.control.cursor_anchoring
                    pre_contact = False
                    if pointer_det is not None and pointer_det.contact_distance is not None:
                        pre_contact = bool(float(pointer_det.contact_distance) <= float(anch.pre_contact_threshold))

                    dbg = {}
                    try:
                        if self._controller is not None:
                            dbg = self._controller.get_debug_state()
                    except Exception:
                        dbg = {}

                    line1 = (
                        f"pie={int(bool(self._pie_active))} "
                        f"ptr={getattr(pointer_det, 'handedness', None)} "
                        f"cmd={getattr(command_det, 'handedness', None)} "
                        f"mode={getattr(pointer_det, 'mode', 'None')}  fps={fps:.1f}  lat={float(getattr(pointer_det, 'latency_ms', 0.0)):.1f}ms"
                    )
                    line2 = (
                        f"c={int(bool(getattr(pointer_det, 'contact', False)))} pre={int(bool(pre_contact))} "
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
        opened = open_camera(settings.camera.device_id)
        cap = opened.cap
        if cap is None or (not cap.isOpened()):
            self._cap = None
            tried = ", ".join(opened.tried_backends) if opened.tried_backends else "default"
            self._log_event(
                "ERROR",
                "camera.open_failed",
                {
                    "device_id": int(settings.camera.device_id),
                    "tried_backends": list(opened.tried_backends),
                },
            )
            self.error.emit(
                f"カメラを開けませんでした（device_id/権限/占有を確認）。試行バックエンド: {tried}"
            )
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(settings.camera.width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(settings.camera.height))
        cap.set(cv2.CAP_PROP_FPS, float(settings.camera.fps))
        self._cap = cap
        self._log_event(
            "INFO",
            "camera.opened",
            {
                "device_id": int(settings.camera.device_id),
                "backend": opened.backend_name,
                "tried_backends": list(opened.tried_backends),
                "requested_width": int(settings.camera.width),
                "requested_height": int(settings.camera.height),
                "requested_fps": int(settings.camera.fps),
            },
        )
        self._last_capture_error_ms = 0

    def _close_camera(self) -> None:
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = None

    def _log_event(self, level: str, event: str, data: dict) -> None:
        try:
            if self._log_manager is not None:
                self._log_manager.logger.write(level, event, data)
        except Exception:
            pass

    @staticmethod
    def _pick_fallback(primary, secondary):
        return primary if primary is not None else secondary

    def _assign_roles(self, dual: DualHandResult, settings: Settings):
        """利き手設定に基づき、pointer(利き手)とcommand(非利き手)を返す。"""

        dom = str(getattr(settings, "dominant_hand", "right")).strip().lower()
        if dom == "left":
            # 役割混線を防ぐため、handednessが確定した側だけを採用する（フォールバックしない）
            pointer = dual.left
            command = dual.right
        else:
            pointer = dual.right
            command = dual.left
        return pointer, command

    @staticmethod
    def _palm_center_y_from_landmarks(hand_landmarks) -> Optional[float]:
        if hand_landmarks is None:
            return None
        lm = hand_landmarks
        try:
            return float((lm[0].y + lm[5].y + lm[17].y) / 3.0)
        except Exception:
            return None

    def _compute_preset_step(self, det, settings: Settings, now_ms: int) -> int:
        """PieMenu表示中の利き手スクロールを、プリセット切替ステップへ変換する。

        仕様:
        - OSスクロールは行わず、上下移動に応じて preset を循環切替する。
        - フレーム揺れで連打にならないよう、しきい値＋クールダウンを設ける。
        """

        # 防御: 利き手（dominant_hand）以外のScrollではプリセットを切り替えない
        dom = str(getattr(settings, "dominant_hand", "right")).strip().lower()
        need = "Left" if dom == "left" else "Right"
        if str(getattr(det, "handedness", "")) != need:
            self._pie_scroll_prev_center_y = None
            return 0

        center_y = self._palm_center_y_from_landmarks(getattr(det, "hand_landmarks", None))
        if center_y is None:
            self._pie_scroll_prev_center_y = None
            return 0

        if self._pie_scroll_prev_center_y is None:
            self._pie_scroll_prev_center_y = float(center_y)
            return 0

        dy = float(center_y) - float(self._pie_scroll_prev_center_y)
        self._pie_scroll_prev_center_y = float(center_y)

        # 1回の切替に必要な移動量（正規化）。大きめにして意図的な操作のみ拾う。
        step_th = 0.03
        cooldown_ms = 180
        if now_ms - int(self._pie_scroll_last_step_ms) < cooldown_ms:
            return 0

        if dy <= -step_th:
            self._pie_scroll_last_step_ms = int(now_ms)
            return +1
        if dy >= step_th:
            self._pie_scroll_last_step_ms = int(now_ms)
            return -1
        return 0


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

