from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Deque, Optional, Tuple
from collections import deque

import pyautogui

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

        # 押しっぱなしを残さない（安全側）
        try:
            pyautogui.mouseUp()
        except Exception:
            pass

    def update(self, det: DetectionResult) -> ControlOutput:
        """検出結果を受け取り、必要なOS操作を発行する。"""

        settings = self._store.get()
        now_ms = int(time.time() * 1000)

        moved = scrolled = left_clicked = right_clicked = drag_down = drag_up = anchored = False

        # Mouseモード以外では、相対移動の状態（前フレーム座標/EMA）を残さない。
        if det.mode != "Mouse":
            self._mouse_prev_hand_xy = None
            self._ema.reset()

        # Mouseモードは誤判定/揺れがあり得るため、連続フレームで安定してから操作を開始する。
        # 「指を立てていないのに座標が反映される」問題の多くは、ここで弾ける。
        if det.mode == "Mouse" and det.pointer_xy is not None:
            self._mouse_mode_streak += 1
        else:
            self._mouse_mode_streak = 0

        # モード遷移時に「開始地点」を確定する（相対操作）
        if det.mode != self._prev_mode:
            self._on_mode_change(det)
            self._prev_mode = det.mode

        # Scrollは排他：移動やクリック系は無視
        if det.mode == "Scroll":
            scrolled = self._handle_scroll(det, settings)
            # Scrollに入ったらクリック系状態は捨てる（誤動作防止）
            self._reset_contact_queue_state()
            anchored = False
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

        # --- Cursor Anchoring（接触中 or 予兆） ---
        anch_cfg = settings.control.cursor_anchoring
        pre_contact = (
            anch_cfg.enabled
            and (det.contact_distance is not None)
            and (float(det.contact_distance) <= float(anch_cfg.pre_contact_threshold))
        )
        middle_bent = not bool(det.middle_extended)

        # クリック姿勢ラッチ（middleが曲がったら有効化、明確に解除条件が来るまで保持）
        if middle_bent:
            self._click_pose_active = True
        # contact/pre_contact中は「クリックするつもり」の可能性が高いので、解除しない
        if (not middle_bent) and (not bool(det.contact)) and (not pre_contact):
            self._click_pose_active = False

        suppress_move_middle = bool(settings.control.move_suppress_on_middle_bent) and self._click_pose_active

        anchoring_active = bool(anch_cfg.enabled) and (bool(det.contact) or pre_contact or suppress_move_middle)
        anchored = anchoring_active

        # 合意仕様: 予兆(pre_contact)に入った瞬間からカーソルを固定する（精度最優先）。
        # ここで「固定開始/解除」の遷移を明確にし、解除後のジャンプ（Δの積分）を防ぐ。
        if anchoring_active and not self._anchored:
            cur = pyautogui.position()
            self._anchored_cursor_xy = (float(cur.x), float(cur.y))
            self._anchored = True
            # 固定開始時点での手位置をprevとして採用し、固定中にΔが溜まらないようにする
            if det.pointer_xy is not None:
                self._mouse_prev_hand_xy = det.pointer_xy
            self._ema.reset()
        elif (not anchoring_active) and self._anchored:
            # 固定解除：解除直後の1フレームでΔが大きくならないようprevを更新
            self._anchored = False
            self._anchored_cursor_xy = None
            if det.pointer_xy is not None:
                self._mouse_prev_hand_xy = det.pointer_xy
            self._ema.reset()

        # --- マウス移動 ---
        stable_frames = max(1, int(settings.control.mouse_mode_stable_frames))
        if det.mode == "Mouse" and det.pointer_xy is not None and self._mouse_mode_streak >= stable_frames:
            moved = self._handle_move(det.pointer_xy, settings, anchoring_active)

        # --- Action Queueing（タップ/ドラッグ） ---
        # クリック系は「中指が曲がっている」ことを必須条件にする（誤操作防止）
        if bool(settings.control.click_requires_middle_bent) and (not self._click_pose_active):
            lc = rc = dd = du = False
            # 状態が残って誤発火しないようリセット
            self._reset_contact_queue_state()
        else:
            lc, rc, dd, du = self._handle_contact_actions(det, settings, now_ms)
        left_clicked |= lc
        right_clicked |= rc
        drag_down |= dd
        drag_up |= du

        out = ControlOutput(
            moved=moved,
            scrolled=scrolled,
            left_clicked=left_clicked,
            right_clicked=right_clicked,
            drag_down=drag_down,
            drag_up=drag_up,
            anchored=anchored,
        )
        if self._logger is not None and any(dat for dat in out.__dict__.values()):
            self._logger.write("INFO", "control.mouse", out.__dict__)
        return out

    def _handle_move(self, pointer_xy: Tuple[float, float], settings: Settings, anchoring_active: bool) -> bool:
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
            if self._anchored_cursor_xy is not None:
                ax, ay = self._anchored_cursor_xy
                try:
                    pyautogui.moveTo(int(ax), int(ay))
                except Exception:
                    pass
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

        # ノイズ抑制（デッドゾーン）＋急ジャンプ抑制（クランプ）
        deadzone = float(settings.control.relative_move_deadzone)
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

        cur = pyautogui.position()
        target_x = float(cur.x) + (dx_norm * float(screen_w) * sens_x)
        target_y = float(cur.y) + (dy_norm * float(screen_h) * sens_y)

        self._ema.alpha = float(settings.control.smoothing_factor)
        x_s, y_s = self._ema.update(target_x, target_y)

        pyautogui.moveTo(int(x_s), int(y_s))
        return True

    def get_debug_state(self) -> dict:
        """動作確認用に内部状態を返す（testsで表示に使用）。"""

        return {
            "prev_mode": self._prev_mode,
            "mouse_mode_streak": self._mouse_mode_streak,
            "prev_hand_xy": self._mouse_prev_hand_xy,
            "anchored": self._anchored,
            "anchored_cursor_xy": self._anchored_cursor_xy,
            "click_pose_active": self._click_pose_active,
        }

    def _handle_contact_actions(
        self, det: DetectionResult, settings: Settings, now_ms: int
    ) -> Tuple[bool, bool, bool, bool]:
        """接触イベント履歴からクリック/ドラッグを判定する。"""

        left_clicked = right_clicked = drag_down = drag_up = False
        tap_interval = int(settings.control.tap_interval_ms)

        contact = bool(det.contact)
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
                try:
                    pyautogui.mouseUp()
                except Exception:
                    pass
                self._dragging = False
                drag_up = True
                # ドラッグ解除はクリック判定に混ぜない
                self._tap_times_ms.clear()
            else:
                # 短い接触はタップとしてキューへ
                if hold_ms <= tap_interval:
                    self._tap_times_ms.append(now_ms)

            self._contact_on_ms = None

        # 接触継続中：一定時間以上ならドラッグ開始
        if contact and not self._dragging and self._contact_on_ms is not None:
            hold_ms = now_ms - self._contact_on_ms
            if hold_ms > tap_interval:
                try:
                    pyautogui.mouseDown()
                except Exception:
                    pass
                self._dragging = True
                drag_down = True
                # ドラッグが始まったらタップ履歴は破棄（誤発火防止）
                self._tap_times_ms.clear()

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

                if count >= 3:
                    try:
                        pyautogui.click(button="right")
                    except Exception:
                        pass
                    right_clicked = True
                elif count >= 2:
                    try:
                        pyautogui.click(button="left")
                    except Exception:
                        pass
                    left_clicked = True
                self._tap_times_ms.clear()

        self._contact_prev = contact
        return left_clicked, right_clicked, drag_down, drag_up

    def _handle_scroll(self, det: DetectionResult, settings: Settings) -> bool:
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

    def _reset_contact_queue_state(self) -> None:
        self._contact_prev = False
        self._contact_on_ms = None
        self._tap_times_ms.clear()
        if self._dragging:
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
        self._dragging = False

