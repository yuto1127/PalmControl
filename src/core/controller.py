from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Deque, Optional, Tuple
from collections import deque

import pyautogui
from pynput.mouse import Button, Controller as PnMouse

from src.core.detector import DetectionResult
from src.utils.config_loader import ConfigStore, Settings, get_config_store
from src.utils.filters import EMAFilter
from src.utils.logger import JsonPrependLogger


@dataclass(frozen=True)
class ControlOutput:
    """Controllerがそのフレームで発行した操作の要約（デバッグ/ログ用）。"""

    moved: bool
    scrolled: bool
    left_clicked: bool
    right_clicked: bool
    drag_down: bool
    drag_up: bool
    anchored: bool


class MouseController:
    """解析結果をOS操作へ変換するコントローラー。

    重要な意図（spec 4.3.4）:
    - **Finger Count Priority**により、Scroll中はポインタ移動を無視して干渉を防ぐ。
    - **Action Queueing**で「ドラッグ」と「連続タップ（左/右クリック）」を時間軸で判別する。
    - **Cursor Anchoring**で、接触動作の瞬間に起きる手ブレによる誤移動を抑える。
    """

    def __init__(
        self,
        store: Optional[ConfigStore] = None,
        *,
        logger: Optional[JsonPrependLogger] = None,
    ) -> None:
        self._store = store or get_config_store("config/settings.yaml")
        self._logger = logger

        # PyAutoGUIはデフォルトで各操作後に待ち時間(PAUSE)が入るため、
        # リアルタイム制御ではカクつきの主因になりやすい。ここでは待ち時間を無効化する。
        pyautogui.PAUSE = 0.0

        settings = self._store.get()
        self._ema = EMAFilter(alpha=float(settings.control.smoothing_factor))

        # macOS環境では pyautogui の mouseDown 状態がドラッグとして安定しないことがある。
        # 押下/解放/移動は pynput を優先し、画面サイズ取得などは pyautogui を継続利用する。
        self._mouse = PnMouse()

        self._dragging = False
        self._contact_prev = False
        self._contact_on_ms: Optional[int] = None
        self._tap_times_ms: Deque[int] = deque(maxlen=8)  # タップ確定（離脱）時刻の履歴

        self._scroll_prev_center_y: Optional[float] = None

        # 相対操作（空中トラックパッド方式）:
        # - pointer_xyの「絶対値」を目標座標にせず、フレーム間Δのみを積分してカーソルを動かす。
        # - モード切替時の座標ジャンプがあっても「開始直後の吸い付き」が起きにくい。
        self._prev_mode: str = "None"
        self._mouse_prev_hand_xy: Optional[Tuple[float, float]] = None
        self._scroll_prev_center_y: Optional[float] = None
        self._mouse_mode_streak: int = 0

        # アンカー状態（pre_contact/contact で座標固定）
        self._anchored: bool = False
        self._anchored_cursor_xy: Optional[Tuple[float, float]] = None

        # クリック姿勢（中指を曲げる）をラッチする。
        # middle伸展判定はフレームごとに揺れることがあるため、
        # 「一瞬だけ伸びた」でクリックが無効化されるのを防ぐ。
        self._click_pose_active: bool = False

        # 中指を「曲げ始めた瞬間」に人差し指先が引っ張られてカーソルが動くことがある。
        # middle TIP(12) の下方向移動を検知したら短時間だけアンカー（移動停止）を入れる。
        self._prev_middle_tip_y: Optional[float] = None
        self._middle_motion_freeze_until_ms: int = 0

        # ドラッグ中の接触（pinch）が一瞬OFFになるのを吸収するためのラッチ
        self._drag_effective_contact: bool = False
        self._drag_contact_off_since_ms: Optional[int] = None
        self._drag_contact_off_frames: int = 0

        # GUIデバッグ用スナップショット（毎フレーム更新）
        self._dbg_apply_actions: bool = True
        self._dbg_pre_contact: bool = False
        self._dbg_anchoring_freeze: bool = False
        self._dbg_anchoring_active_raw: bool = False
        self._dbg_suppress_move_middle: bool = False
        self._dbg_contact_hold_ms: Optional[int] = None
        self._dbg_drag_raw_contact: bool = False
        self._dbg_drag_effective_contact: bool = False

    def reset(self) -> None:
        """内部状態を全リセット（安全停止/再開時用）。"""

        self._ema.reset()
        self._dragging = False
        self._contact_prev = False
        self._contact_on_ms = None
        self._tap_times_ms.clear()
        self._scroll_prev_center_y = None
        self._prev_mode = "None"
        self._mouse_prev_hand_xy = None
        self._mouse_mode_streak = 0
        self._anchored = False
        self._anchored_cursor_xy = None
        self._click_pose_active = False
        self._prev_middle_tip_y = None
        self._middle_motion_freeze_until_ms = 0
        self._drag_effective_contact = False
        self._drag_contact_off_since_ms = None
        self._drag_contact_off_frames = 0

        # 押しっぱなしを残さない（安全側）
        try:
            self._mouse.release(Button.left)
        except Exception:
            pass
        try:
            self._mouse.release(Button.right)
        except Exception:
            pass

    @staticmethod
    def _mouse_position_xy() -> Tuple[float, float]:
        """現在のカーソル座標を取得する。"""
        try:
            p = pyautogui.position()
            return float(p.x), float(p.y)
        except Exception:
            return 0.0, 0.0

    def _mouse_move_to(self, x: int, y: int, *, apply_actions: bool) -> None:
        if not apply_actions:
            return
        try:
            self._mouse.position = (int(x), int(y))
        except Exception:
            try:
                pyautogui.moveTo(int(x), int(y))
            except Exception:
                pass

    def _mouse_down_left(self, *, apply_actions: bool) -> None:
        if not apply_actions:
            return
        try:
            self._mouse.press(Button.left)
        except Exception:
            try:
                pyautogui.mouseDown(button="left")
            except Exception:
                pass

    def _mouse_up_left(self, *, apply_actions: bool) -> None:
        if not apply_actions:
            return
        try:
            self._mouse.release(Button.left)
        except Exception:
            try:
                pyautogui.mouseUp(button="left")
            except Exception:
                pass

    def _mouse_click(self, button: str, *, apply_actions: bool) -> None:
        if not apply_actions:
            return
        b = Button.right if button == "right" else Button.left
        try:
            self._mouse.click(b, 1)
        except Exception:
            try:
                pyautogui.click(button=button)
            except Exception:
                pass

    def _mouse_double_click_left(self, *, apply_actions: bool) -> None:
        """左ダブルクリック（OS側のダブルクリックとして認識されるよう2回送信）。"""
        if not apply_actions:
            return
        try:
            self._mouse.click(Button.left, 2)
        except Exception:
            try:
                pyautogui.doubleClick()
            except Exception:
                # 最後の手段: 2回クリック
                try:
                    pyautogui.click(button="left")
                    pyautogui.click(button="left")
                except Exception:
                    pass

    def update(self, det: DetectionResult, *, apply_actions: bool = True) -> ControlOutput:
        """検出結果を受け取り、必要なOS操作を発行する。"""

        settings = self._store.get()
        now_ms = int(time.time() * 1000)
        act = bool(apply_actions)
        self._dbg_apply_actions = act

        moved = scrolled = left_clicked = right_clicked = drag_down = drag_up = anchored = False

        # Mouseモード以外では、相対移動の状態（前フレーム座標/EMA）を残さない。
        # ただしドラッグ中は「つまみ姿勢」でMouse判定が一瞬落ちることがあり、毎フレームリセットすると移動が成立しない。
        if det.mode != "Mouse" and (not self._dragging):
            self._mouse_prev_hand_xy = None
            self._ema.reset()
            self._prev_middle_tip_y = None
            self._middle_motion_freeze_until_ms = 0

        # Mouseモードは誤判定/揺れがあり得るため、連続フレームで安定してから操作を開始する。
        # 「指を立てていないのに座標が反映される」問題の多くは、ここで弾ける。
        if det.mode == "Mouse" and det.pointer_xy is not None:
            self._mouse_mode_streak += 1
        elif self._dragging:
            # ドラッグ中はMouse判定が揺れても streak をゼロに戻さない（移動開始条件のブレ防止）
            self._mouse_mode_streak = max(self._mouse_mode_streak, int(settings.control.mouse_mode_stable_frames))
        else:
            self._mouse_mode_streak = 0

        # モード遷移時に「開始地点」を確定する（相対操作）
        if det.mode != self._prev_mode:
            self._on_mode_change(det)
            self._prev_mode = det.mode

        # Scrollは排他：移動やクリック系は無視
        if det.mode == "Scroll":
            scrolled = self._handle_scroll(det, settings, apply_actions=act)
            # Scrollに入ったらクリック系状態は捨てる（誤動作防止）
            self._reset_contact_queue_state(apply_actions=act)
            anchored = False
            # デバッグ表示用スナップショット（Scroll中はクリック系は無効）
            self._dbg_pre_contact = False
            self._dbg_anchoring_active_raw = False
            self._dbg_anchoring_freeze = False
            self._dbg_suppress_move_middle = False
            self._dbg_contact_hold_ms = None
            return ControlOutput(
                moved=False,
                scrolled=scrolled,
                left_clicked=False,
                right_clicked=False,
                drag_down=False,
                drag_up=False,
                anchored=False,
            )

        # Mouse/None：スクロール基準はリセット
        if det.mode != "Scroll":
            self._scroll_prev_center_y = None

        # --- Action Queueing（タップ/ドラッグ） ---
        # クリック系は「中指が曲がっている」ことを必須条件にする（誤操作防止）
        if bool(settings.control.click_requires_middle_bent) and (not self._click_pose_active) and (not self._dragging):
            lc = rc = dd = du = False
            # 状態が残って誤発火しないようリセット
            self._reset_contact_queue_state(apply_actions=act)
        else:
            raw_contact = bool(det.contact)
            effective_contact = self._effective_drag_contact(raw_contact, settings, now_ms)
            self._dbg_drag_raw_contact = bool(raw_contact)
            self._dbg_drag_effective_contact = bool(effective_contact)

            lc, rc, dd, du = self._handle_contact_actions(
                det, settings, now_ms, apply_actions=act, effective_contact=effective_contact
            )
        left_clicked |= lc
        right_clicked |= rc
        drag_down |= dd
        drag_up |= du

        # --- Cursor Anchoring（接触中 or 予兆） ---
        anch_cfg = settings.control.cursor_anchoring
        pre_contact = (
            anch_cfg.enabled
            and (det.contact_distance is not None)
            and (float(det.contact_distance) <= float(anch_cfg.pre_contact_threshold))
        )
        middle_bent = not bool(det.middle_extended)

        # 中指の「下方向」への動きを検知して、クリック開始前の短い区間も固定する
        middle_motion_freeze = False
        try:
            if det.mode == "Mouse" and det.hand_landmarks is not None:
                lm = det.hand_landmarks
                y = float(lm[12].y)
                if self._prev_middle_tip_y is not None:
                    dy_tip = float(y) - float(self._prev_middle_tip_y)
                    # 画像座標は下方向が+。中指が曲がる動作は TIP が下がりやすい。
                    if dy_tip > 0.008:
                        self._middle_motion_freeze_until_ms = max(
                            self._middle_motion_freeze_until_ms, int(now_ms + 180)
                        )
                self._prev_middle_tip_y = float(y)
            else:
                self._prev_middle_tip_y = None
        except Exception:
            self._prev_middle_tip_y = None

        if int(now_ms) < int(self._middle_motion_freeze_until_ms):
            middle_motion_freeze = True

        # クリック姿勢ラッチ（middleが曲がったら有効化、明確に解除条件が来るまで保持）
        if middle_bent:
            self._click_pose_active = True
        # contact/pre_contact中は「クリックするつもり」の可能性が高いので、解除しない
        if (not middle_bent) and (not bool(det.contact)) and (not pre_contact):
            self._click_pose_active = False

        suppress_move_middle = bool(settings.control.move_suppress_on_middle_bent) and self._click_pose_active

        # ドラッグ中は「中指曲げで移動抑制」をアンカー固定に使わない（範囲選択の移動を阻害しやすいため）
        suppress_move_middle_for_anchor = suppress_move_middle and (not self._dragging)

        # middle_motion_freeze を無条件で足すと、縦移動で中指TIPの y が毎フレーム大きく変わり
        # アンカーが連続して「上下だけ効かない」状態になりやすい。
        # クリック文脈（クリック予備姿勢・親指予兆・接触）があるときだけフリーズをアンカーに効かせる。
        middle_motion_freeze_for_anchor = bool(middle_motion_freeze) and (
            self._click_pose_active or pre_contact or bool(det.contact)
        )

        anchoring_active_raw = bool(anch_cfg.enabled) and (
            bool(det.contact)
            or pre_contact
            or suppress_move_middle_for_anchor
            or middle_motion_freeze_for_anchor
        )
        # 要望: ドラッグ（mouseDown確定）後は移動を許可するため、アンカーによる移動停止を無効化する。
        anchoring_freeze = bool(anchoring_active_raw) and (not self._dragging)
        anchored = anchoring_freeze

        # 合意仕様: 予兆(pre_contact)に入った瞬間からカーソルを固定する（精度最優先）。
        # ただし、ドラッグ確定後は固定を解除して範囲選択を可能にする。
        if anchoring_freeze and not self._anchored:
            if act:
                cx, cy = self._mouse_position_xy()
                self._anchored_cursor_xy = (float(cx), float(cy))
            else:
                self._anchored_cursor_xy = None
            self._anchored = True
            # 固定開始時点での手位置をprevとして採用し、固定中にΔが溜まらないようにする
            if det.pointer_xy is not None:
                self._mouse_prev_hand_xy = det.pointer_xy
            self._ema.reset()
        elif (not anchoring_freeze) and self._anchored:
            # 固定解除：解除直後の1フレームでΔが大きくならないようprevを更新
            self._anchored = False
            self._anchored_cursor_xy = None
            if det.pointer_xy is not None:
                self._mouse_prev_hand_xy = det.pointer_xy
            self._ema.reset()

        # --- マウス移動 ---
        stable_frames = max(1, int(settings.control.mouse_mode_stable_frames))
        move_ok = (det.mode == "Mouse" and det.pointer_xy is not None) or self._dragging
        streak_ok = self._dragging or (self._mouse_mode_streak >= stable_frames)
        pointer_xy = det.pointer_xy
        if pointer_xy is None and self._dragging and det.hand_landmarks is not None:
            # ドラッグ中にMouse判定が落ちても、ランドマークがあればポインタを再計算して移動を継続する
            pointer_xy = self._compute_pointer_xy_from_landmarks(det.hand_landmarks, settings)

        if move_ok and pointer_xy is not None and streak_ok:
            moved = self._handle_move(pointer_xy, settings, anchoring_freeze, apply_actions=act)

        hold_ms_dbg: Optional[int] = None
        if self._contact_on_ms is not None:
            hold_ms_dbg = max(0, int(now_ms - int(self._contact_on_ms)))

        self._dbg_pre_contact = bool(pre_contact)
        self._dbg_anchoring_active_raw = bool(anchoring_active_raw)
        self._dbg_anchoring_freeze = bool(anchoring_freeze)
        self._dbg_suppress_move_middle = bool(suppress_move_middle)
        self._dbg_contact_hold_ms = hold_ms_dbg

        out = ControlOutput(
            moved=moved,
            scrolled=scrolled,
            left_clicked=left_clicked,
            right_clicked=right_clicked,
            drag_down=drag_down,
            drag_up=drag_up,
            anchored=anchored,
        )
        if act and self._logger is not None and any(dat for dat in out.__dict__.values()):
            self._logger.write("INFO", "control.mouse", out.__dict__)
        return out

    @staticmethod
    def _compute_pointer_xy_from_landmarks(hand_landmarks, settings: Settings) -> Optional[Tuple[float, float]]:
        """Mouseモード用ポインタ座標を、ランドマークから再計算する（ドラッグ中のフォールバック）。"""
        try:
            lm = hand_landmarks
            src = str(getattr(settings.control, "pointer_source", "index_middle_avg"))
            if src == "wrist":
                x = float(lm[0].x)
                y = float(lm[0].y)
            elif src == "index_tip":
                x = float(lm[8].x)
                y = float(lm[8].y)
            elif src == "index_middle_avg":
                x = (float(lm[8].x) + float(lm[12].x)) / 2.0
                y = (float(lm[8].y) + float(lm[12].y)) / 2.0
            else:
                # 互換: 未知値は従来挙動（平均）
                x = (float(lm[8].x) + float(lm[12].x)) / 2.0
                y = (float(lm[8].y) + float(lm[12].y)) / 2.0

            roi = settings.camera.roi
            if roi.enabled:
                x = (x - roi.x) / max(roi.w, 1e-9)
                y = (y - roi.y) / max(roi.h, 1e-9)

            # GUIプレビューと同様、ユーザー視点で直感的になるようXを反転する
            x = 1.0 - float(x)
            return (float(x), float(y))
        except Exception:
            return None

    def _handle_move(
        self, pointer_xy: Tuple[float, float], settings: Settings, anchoring_active: bool, *, apply_actions: bool
    ) -> bool:
        screen_w, screen_h = pyautogui.size()
        sens_x = float(settings.control.sensitivity_x)
        sens_y = float(settings.control.sensitivity_y)

        # クリック/タップ直前〜接触中は「座標固定」を最優先する。
        # 相対移動方式では、接触動作中の微小な手ブレがΔとして積分されやすく、
        # 「クリック時にカーソルが逃げる」原因になりがち。
        if anchoring_active:
            # 次のフレームでΔが大きくならないよう、前回手座標だけ更新して移動しない。
            self._mouse_prev_hand_xy = pointer_xy
            # EMAもリセットして、解除直後の追従で余計な残りが出ないようにする。
            self._ema.reset()
            # 念のため「固定開始時点のカーソル位置」に戻す（ごく稀なズレ/競合対策）
            if apply_actions and self._anchored_cursor_xy is not None:
                ax, ay = self._anchored_cursor_xy
                self._mouse_move_to(int(ax), int(ay), apply_actions=apply_actions)
            return False

        # 前フレームとの差分Δで動かす
        if self._mouse_prev_hand_xy is None:
            self._mouse_prev_hand_xy = pointer_xy
            self._ema.reset()
            return False

        prev_x, prev_y = self._mouse_prev_hand_xy
        dx_norm = float(pointer_xy[0]) - float(prev_x)
        dy_norm = float(pointer_xy[1]) - float(prev_y)
        self._mouse_prev_hand_xy = pointer_xy

        # 正規化空間では縦方向のΔが横より小さく出やすい（ROI・構図）。既定で軽くブーストする。
        dy_norm *= float(settings.control.relative_move_vertical_gain)

        # ノイズ抑制（デッドゾーン）＋急ジャンプ抑制（クランプ）
        deadzone = float(settings.control.relative_move_deadzone)
        # move_suppress_on_middle_bent: クリック予備姿勢では完全固定せず、微小Δだけやや鈍くする
        if bool(settings.control.move_suppress_on_middle_bent) and self._click_pose_active and (not self._dragging):
            deadzone *= 2.5
        if abs(dx_norm) < deadzone:
            dx_norm = 0.0
        if abs(dy_norm) < deadzone:
            dy_norm = 0.0
        if dx_norm == 0.0 and dy_norm == 0.0:
            return False

        clamp_th = float(settings.control.relative_move_clamp_th)
        if dx_norm > clamp_th:
            dx_norm = clamp_th
        elif dx_norm < -clamp_th:
            dx_norm = -clamp_th
        if dy_norm > clamp_th:
            dy_norm = clamp_th
        elif dy_norm < -clamp_th:
            dy_norm = -clamp_th

        if apply_actions:
            cx, cy = self._mouse_position_xy()
            cur = type("Pos", (), {"x": cx, "y": cy})()
        else:
            # dry-runでは画面座標に依存させない（ヘッドレス等でも状態遷移を追えるように）
            cur_x = float(self._anchored_cursor_xy[0]) if self._anchored_cursor_xy is not None else 0.0
            cur_y = float(self._anchored_cursor_xy[1]) if self._anchored_cursor_xy is not None else 0.0
            cur = type("Pos", (), {"x": cur_x, "y": cur_y})()

        target_x = float(cur.x) + (dx_norm * float(screen_w) * sens_x)
        target_y = float(cur.y) + (dy_norm * float(screen_h) * sens_y)

        self._ema.alpha = float(settings.control.smoothing_factor)
        x_s, y_s = self._ema.update(target_x, target_y)

        if apply_actions:
            self._mouse_move_to(int(x_s), int(y_s), apply_actions=True)
        return bool(apply_actions)

    def get_debug_state(self) -> dict:
        """動作確認用に内部状態を返す（testsで表示に使用）。"""

        return {
            "apply_actions": bool(self._dbg_apply_actions),
            "prev_mode": self._prev_mode,
            "mouse_mode_streak": self._mouse_mode_streak,
            "prev_hand_xy": self._mouse_prev_hand_xy,
            "anchored": self._anchored,
            "anchored_cursor_xy": self._anchored_cursor_xy,
            "click_pose_active": self._click_pose_active,
            "dragging": bool(self._dragging),
            "contact_prev": bool(self._contact_prev),
            "contact_on_ms": self._contact_on_ms,
            "contact_hold_ms": self._dbg_contact_hold_ms,
            "tap_queue_len": len(self._tap_times_ms),
            "pre_contact": bool(self._dbg_pre_contact),
            "anchoring_active_raw": bool(self._dbg_anchoring_active_raw),
            "anchoring_freeze": bool(self._dbg_anchoring_freeze),
            "suppress_move_middle": bool(self._dbg_suppress_move_middle),
            "drag_raw_contact": bool(self._dbg_drag_raw_contact),
            "drag_effective_contact": bool(self._dbg_drag_effective_contact),
            "drag_contact_off_frames": int(self._drag_contact_off_frames),
        }

    def _effective_drag_contact(self, raw_contact: bool, settings: Settings, now_ms: int) -> bool:
        """ドラッグ中の接触を安定化する（短いOFFを吸収）。"""
        if not self._dragging:
            self._drag_effective_contact = bool(raw_contact)
            self._drag_contact_off_since_ms = None
            self._drag_contact_off_frames = 0
            return bool(raw_contact)

        grace_ms = max(0, int(getattr(settings.control, "drag_contact_grace_ms", 0)))
        need_frames = max(1, int(getattr(settings.control, "drag_contact_release_frames", 1)))

        if raw_contact:
            self._drag_effective_contact = True
            self._drag_contact_off_since_ms = None
            self._drag_contact_off_frames = 0
            return True

        # OFF開始
        if self._drag_contact_off_since_ms is None:
            self._drag_contact_off_since_ms = now_ms
        self._drag_contact_off_frames += 1

        off_ms = now_ms - int(self._drag_contact_off_since_ms)

        # 短いOFFは維持
        if grace_ms > 0 and off_ms < grace_ms:
            return bool(self._drag_effective_contact)

        # ある程度OFFが続いたら離脱扱い
        if self._drag_contact_off_frames >= need_frames:
            self._drag_effective_contact = False
            return False

        return bool(self._drag_effective_contact)

    def _handle_contact_actions(
        self,
        det: DetectionResult,
        settings: Settings,
        now_ms: int,
        *,
        apply_actions: bool,
        effective_contact: bool,
    ) -> Tuple[bool, bool, bool, bool]:
        """接触イベント履歴からクリック/ドラッグを判定する。"""

        left_clicked = right_clicked = drag_down = drag_up = False
        tap_interval = int(settings.control.tap_interval_ms)
        drag_hold_ms = int(getattr(settings.control, "drag_hold_ms", tap_interval))

        contact = bool(effective_contact)
        if contact and not self._contact_prev:
            # 接触開始
            self._contact_on_ms = now_ms
        elif (not contact) and self._contact_prev:
            # 離脱（接触→離脱を1タップとして数える）
            if self._contact_on_ms is not None:
                hold_ms = now_ms - self._contact_on_ms
            else:
                hold_ms = 0

            # ドラッグ中なら離脱でmouseUp
            if self._dragging:
                self._mouse_up_left(apply_actions=apply_actions)
                self._dragging = False
                drag_up = True
                # ドラッグ解除はクリック判定に混ぜない
                self._tap_times_ms.clear()
                self._drag_effective_contact = False
                self._drag_contact_off_since_ms = None
                self._drag_contact_off_frames = 0
            else:
                # 短い接触はタップとしてキューへ
                if hold_ms <= tap_interval:
                    self._tap_times_ms.append(now_ms)

            self._contact_on_ms = None

        # 接触継続中：一定時間以上ならドラッグ開始
        #
        # 重要:
        # - `apply_actions=False`（dry-run）でも内部状態は更新される設計だが、
        #   ここでドラッグ状態だけを立てると、タップ履歴が破棄され「連続ピンチのクリック確定」が成立しなくなる。
        # - PieMenu表示中など、OS操作を抑止している間はドラッグ開始を行わない。
        if apply_actions and contact and not self._dragging and self._contact_on_ms is not None:
            hold_ms = now_ms - self._contact_on_ms
            if hold_ms > drag_hold_ms:
                self._mouse_down_left(apply_actions=apply_actions)
                self._dragging = True
                drag_down = True
                # ドラッグが始まったらタップ履歴は破棄（誤発火防止）
                self._tap_times_ms.clear()
                self._drag_effective_contact = True
                self._drag_contact_off_since_ms = None
                self._drag_contact_off_frames = 0

        # タップ確定：最後のタップからtap_interval以内なら連続とみなす
        # ここでは「タップが2/3回揃ったら離脱時に発火」ではなく、タイミングに応じて確定する。
        if (not contact) and (not self._dragging) and self._tap_times_ms:
            # 直近タップから十分時間が経過したら回数を確定
            if now_ms - self._tap_times_ms[-1] > tap_interval:
                # tap_interval内に入っている連続タップ数を数える（末尾から遡る）
                count = 1
                for i in range(len(self._tap_times_ms) - 1, 0, -1):
                    if self._tap_times_ms[i] - self._tap_times_ms[i - 1] <= tap_interval:
                        count += 1
                    else:
                        break

                # 仕様（研究用の明示割当）:
                # - 連続タップが「2回」→ 左シングル
                # - 連続タップが「3回」→ 左ダブル
                # - 連続タップが「4回以上」→ 右クリック
                if count >= 4:
                    self._mouse_click("right", apply_actions=apply_actions)
                    right_clicked = True
                elif count == 3:
                    self._mouse_double_click_left(apply_actions=apply_actions)
                    left_clicked = True
                elif count == 2:
                    self._mouse_click("left", apply_actions=apply_actions)
                    left_clicked = True
                self._tap_times_ms.clear()

        self._contact_prev = contact
        return left_clicked, right_clicked, drag_down, drag_up

    def _handle_scroll(self, det: DetectionResult, settings: Settings, *, apply_actions: bool) -> bool:
        center_y = self._compute_palm_center_y(det)
        if center_y is None:
            return False

        # 相対操作: Scroll開始時点の中心を原点にする
        if self._scroll_origin_center_y is None:
            return False

        dy = float(center_y) - float(self._scroll_origin_center_y)
        # 微小な揺れで勝手にスクロールしないようデッドゾーンを設ける
        if abs(dy) < float(settings.control.scroll_deadzone):
            return False
        # 画面上方向（yが小さくなる）が「上スクロール」になるよう符号を調整する
        scroll_amount = int(-dy * int(settings.control.scroll_sensitivity))
        if scroll_amount != 0:
            if apply_actions:
                try:
                    pyautogui.scroll(scroll_amount)
                except Exception:
                    pass
            return True
        return False

    def _on_mode_change(self, det: DetectionResult) -> None:
        """モード遷移時に相対操作の原点を設定する。"""

        if det.mode == "Mouse" and det.pointer_xy is not None:
            # Mouseに入った瞬間はキャリブレーション（原点設定）のみ行い、移動は開始しない。
            self._mouse_calibrating = True
        else:
            self._mouse_origin_hand_xy = None
            self._mouse_origin_cursor_xy = None
            self._ema.reset()
            self._mouse_calibrating = False

        if det.mode == "Scroll":
            self._scroll_origin_center_y = self._compute_palm_center_y(det)
        else:
            self._scroll_origin_center_y = None

    @staticmethod
    def _compute_palm_center_y(det: DetectionResult) -> Optional[float]:
        """パーム中心のY（正規化）を推定する。

        specでは0,5,17の平均などを例示しているため、それに従う。
        """

        if det.hand_landmarks is None:
            return None
        lm = det.hand_landmarks
        try:
            return float((lm[0].y + lm[5].y + lm[17].y) / 3.0)
        except Exception:
            return None

    def _reset_contact_queue_state(self, *, apply_actions: bool = True) -> None:
        self._contact_prev = False
        self._contact_on_ms = None
        self._tap_times_ms.clear()
        if self._dragging:
            self._mouse_up_left(apply_actions=apply_actions)
        self._dragging = False

