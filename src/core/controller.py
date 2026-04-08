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

        # 相対操作（モード開始時点を基準にΔで動かす）
        self._prev_mode: str = "None"
        self._mouse_origin_hand_xy: Optional[Tuple[float, float]] = None
        self._mouse_origin_cursor_xy: Optional[Tuple[float, float]] = None
        self._scroll_origin_center_y: Optional[float] = None
        self._mouse_settle_frames: int = 0
        self._mouse_mode_streak: int = 0
        self._mouse_settle_sum_x: float = 0.0
        self._mouse_settle_sum_y: float = 0.0
        self._mouse_settle_count: int = 0

    def reset(self) -> None:
        """内部状態を全リセット（安全停止/再開時用）。"""

        self._ema.reset()
        self._dragging = False
        self._contact_prev = False
        self._contact_on_ms = None
        self._tap_times_ms.clear()
        self._scroll_prev_center_y = None
        self._prev_mode = "None"
        self._mouse_origin_hand_xy = None
        self._mouse_origin_cursor_xy = None
        self._scroll_origin_center_y = None
        self._mouse_settle_frames = 0
        self._mouse_mode_streak = 0
        self._mouse_settle_sum_x = 0.0
        self._mouse_settle_sum_y = 0.0
        self._mouse_settle_count = 0

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

        # Mouse/None：スクロール状態はリセット
        self._scroll_origin_center_y = None

        # --- Cursor Anchoring（接触中 or 予兆） ---
        anch_cfg = settings.control.cursor_anchoring
        pre_contact = (
            anch_cfg.enabled
            and (det.contact_distance is not None)
            and (float(det.contact_distance) <= float(anch_cfg.pre_contact_threshold))
        )
        anchoring_active = bool(anch_cfg.enabled) and (bool(det.contact) or pre_contact)
        anchored = anchoring_active

        # --- マウス移動 ---
        stable_frames = max(1, int(settings.control.mouse_mode_stable_frames))
        if det.mode == "Mouse" and det.pointer_xy is not None and self._mouse_mode_streak >= stable_frames:
            moved = self._handle_move(det.pointer_xy, settings, anchoring_active)

        # --- Action Queueing（タップ/ドラッグ） ---
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

        # 相対操作: モード開始時の手位置を原点としてΔで動かす
        if self._mouse_origin_hand_xy is None or self._mouse_origin_cursor_xy is None:
            # 保険：原点が無ければ現時点を原点として設定する
            self._mouse_origin_hand_xy = pointer_xy
            cur = pyautogui.position()
            self._mouse_origin_cursor_xy = (float(cur.x), float(cur.y))
            self._ema.reset()

        # モード突入直後は座標が跳ぶ/揺れることがあるため、数フレームは移動しない。
        # この期間のpointerを平均して「開始手位置」を確定することで、
        # None中の手の移動がMouse開始直後のΔとして乗ってしまう問題を抑える。
        if self._mouse_settle_frames > 0:
            self._mouse_settle_frames -= 1
            self._mouse_settle_sum_x += float(pointer_xy[0])
            self._mouse_settle_sum_y += float(pointer_xy[1])
            self._mouse_settle_count += 1

            if self._mouse_settle_frames == 0 and self._mouse_settle_count > 0:
                avg_x = self._mouse_settle_sum_x / float(self._mouse_settle_count)
                avg_y = self._mouse_settle_sum_y / float(self._mouse_settle_count)
                self._mouse_origin_hand_xy = (avg_x, avg_y)
                # カーソル原点は「Mouseに入った瞬間の位置」を維持する（ここでは更新しない）
            self._ema.reset()
            return False

        ox, oy = self._mouse_origin_hand_xy
        cx0, cy0 = self._mouse_origin_cursor_xy
        dx_norm = float(pointer_xy[0]) - float(ox)
        dy_norm = float(pointer_xy[1]) - float(oy)

        # デッドゾーン:
        # 手を「動かしていないつもり」でも、検出座標はフレームごとに微小に揺れる。
        # 相対操作ではこの揺れがそのままカーソルのドリフトとして現れるため、
        # 一定以下のΔは「静止」とみなして無視する。
        #
        # さらに、静止中は原点を現在値へ追従させる（re-center）ことで、
        # 長時間の微小揺れでも原点がズレ続けないようにする。
        deadzone = 0.006  # 正規化座標での許容揺れ（経験則。必要なら将来設定化）
        if (abs(dx_norm) < deadzone) and (abs(dy_norm) < deadzone):
            self._mouse_origin_hand_xy = pointer_xy
            return False

        # 正規化Δを画面Δへ
        target_x = cx0 + (dx_norm * float(screen_w) * sens_x)
        target_y = cy0 + (dy_norm * float(screen_h) * sens_y)

        # アンカー中はEMAを極端に重くして“固定感”を出す
        if anchoring_active:
            alpha = float(settings.control.cursor_anchoring.override_smoothing_factor_ema)
            tmp = EMAFilter(alpha=max(min(alpha, 1.0), 1e-6))
            # 現在のEMA状態を引き継いでから更新（急なジャンプを避ける）
            tmp._x = self._ema._x
            tmp._y = self._ema._y
            x_s, y_s = tmp.update(target_x, target_y)
            self._ema._x, self._ema._y = tmp._x, tmp._y
        else:
            self._ema.alpha = float(settings.control.smoothing_factor)
            x_s, y_s = self._ema.update(target_x, target_y)

        pyautogui.moveTo(int(x_s), int(y_s))
        return True

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
        if abs(dy) < 0.003:
            return False
        # 画面上方向（yが小さくなる）が「上スクロール」になるよう符号を調整する
        # scrollの感度は将来的に設定化できるが、初期は固定係数とする。
        scroll_amount = int(-dy * 1200)
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
            # いったん現時点を原点候補にし、数フレーム平均で確定する（ドリフト/跳び対策）
            self._mouse_origin_hand_xy = det.pointer_xy
            cur = pyautogui.position()
            self._mouse_origin_cursor_xy = (float(cur.x), float(cur.y))
            # 原点が変わるのでEMAもリセットしてジャンプ/追従遅れを減らす
            self._ema.reset()
            self._mouse_settle_frames = 5
            self._mouse_settle_sum_x = 0.0
            self._mouse_settle_sum_y = 0.0
            self._mouse_settle_count = 0
        else:
            self._mouse_origin_hand_xy = None
            self._mouse_origin_cursor_xy = None
            self._ema.reset()
            self._mouse_settle_frames = 0
            self._mouse_settle_sum_x = 0.0
            self._mouse_settle_sum_y = 0.0
            self._mouse_settle_count = 0

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

