from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import pyautogui
from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from src.utils.config_loader import ConfigStore, PieMenuSlot
from src.core.media_preset import media_actions_by_id


@dataclass(frozen=True)
class PieMenuSelection:
    """PieMenu上で現在選択されているスロット。"""

    preset: int  # 1..3
    slot: int  # 1..8


class PieMenuOverlay(QWidget):
    """透過型のPieMenuオーバーレイ。

    重要な意図:
    - 表示/非表示は「非利き手がパーかどうか」により外部（Worker）から制御される。
    - 非表示時は入力透過にし、通常のPC操作を妨げない。
    - 表示開始瞬間にマウス座標をメニュー中央へワープし、ユーザー視点の「仮想中心」を固定する。
    - スロット選択は、利き手の pointer_xy（正規化座標）の相対変位から角度を算出して行う。
    """

    slotTriggered = pyqtSignal(int, int)  # preset(1..3), slot(1..8)
    presetChanged = pyqtSignal(int)  # preset(1..3)

    def __init__(self, store: ConfigStore) -> None:
        super().__init__(None)
        self._store = store

        self._active = False
        self._center_screen_xy: Optional[Tuple[int, int]] = None
        self._pointer_origin_xy: Optional[Tuple[float, float]] = None
        self._pointer_xy: Optional[Tuple[float, float]] = None

        self._preset: int = 1
        self._selection: Optional[PieMenuSelection] = None
        self._selection_latched: Optional[PieMenuSelection] = None
        self._selection_latch_until_ms: int = 0
        self._action_msg: str = ""
        self._action_msg_until_ms: int = 0
        self._last_click_until_ms: int = 0

        self._init_window()
        self._apply_inert_state()

    def _init_window(self) -> None:
        self.setWindowTitle("PieMenu")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            # macOSでは Tool ウィンドウの表示がアプリをアクティブ化しやすい。
            # ToolTipは「表示してもフォーカスを奪いにくい」性質があるため、オーバーレイ用途に使う。
            | Qt.WindowType.ToolTip
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # サイズは固定（描画を単純にする）。必要なら後で設定化できる。
        self.resize(520, 520)

    def is_active(self) -> bool:
        return bool(self._active)

    def set_active(self, active: bool, *, pointer_xy: Optional[Tuple[float, float]] = None) -> None:
        """表示/非表示を切り替える。

        active=True になった瞬間:
        - 現在カーソル位置を中心にウィンドウを移動
        - カーソルをウィンドウ中心へワープ
        - pointer_xy の原点（相対変位の基準）を固定
        """

        active = bool(active)
        if active == self._active:
            # 既に表示中なら origin は固定したまま pointer だけ更新
            if active:
                self.update_pointer(pointer_xy)
            return

        self._active = active
        if self._active:
            self._pointer_origin_xy = pointer_xy
            self._pointer_xy = pointer_xy
            self._selection = None
            self._selection_latched = None
            self._selection_latch_until_ms = 0
            self._move_to_screen_center_and_warp_center()
            self._apply_active_state()
            self.show()
            # raise_() は環境によってアプリが前面化し、操作対象のアプリからフォーカスが奪われることがあるため避ける
        else:
            self._pointer_origin_xy = None
            self._pointer_xy = None
            self._selection = None
            self._selection_latched = None
            self._selection_latch_until_ms = 0
            self._apply_inert_state()
            self.hide()

        self.update()

    def update_pointer(self, pointer_xy: Optional[Tuple[float, float]]) -> None:
        """利き手の pointer_xy を受け取り、スロット選択を更新する。"""

        if not self._active:
            return
        self._pointer_xy = pointer_xy

        now_ms = int(time.monotonic() * 1000)
        latch_valid = bool(self._selection_latched is not None and now_ms <= int(self._selection_latch_until_ms))

        if pointer_xy is None:
            # クリック姿勢などでpointer_xyが一時的に欠けることがあるため、短時間は直前選択を保持する。
            self._selection = self._selection_latched if latch_valid else None
            self.update()
            return

        if self._pointer_origin_xy is None:
            self._pointer_origin_xy = pointer_xy
            self._selection = None
            self.update()
            return

        dx = float(pointer_xy[0]) - float(self._pointer_origin_xy[0])
        dy = float(pointer_xy[1]) - float(self._pointer_origin_xy[1])

        # 半径が小さい間は「中央（未選択）」扱い
        r = math.sqrt(dx * dx + dy * dy)
        if r < 0.02:
            # 中央に戻った瞬間に確定できなくなるのを防ぐため、短時間だけ直前選択を維持する
            self._selection = self._selection_latched if latch_valid else None
            self.update()
            return

        # 角度（右=0、上=+90、左=180、下=-90）
        ang = math.degrees(math.atan2(-dy, dx))
        # 8分割: 右を1番として時計回りに 1..8
        # セクタ境界を中央に寄せるため、22.5度オフセットを入れる
        idx0 = int(((ang + 360.0 + 22.5) % 360.0) // 45.0)  # 0..7
        slot = idx0 + 1
        self._selection = PieMenuSelection(preset=int(self._preset), slot=int(slot))
        # 選択更新が来たらラッチを更新（クリック姿勢移行で選択が消えないようにする）
        self._selection_latched = self._selection
        self._selection_latch_until_ms = int(now_ms + 700)
        self.update()

    def handle_click(self, *, right: bool = False) -> None:
        """クリックイベントを受け取り、スロット実行/中央クリック処理を行う。"""

        if not self._active:
            return
        # 右クリックは現状「同じ実行」扱い（将来別機能にできるよう引数は残す）
        _ = bool(right)

        # デバッグ: クリックが来たこと自体を可視化する
        self._last_click_until_ms = int(time.monotonic() * 1000) + 350

        # 中央（未選択）クリックはプリセット切替に割り当てる
        if self._selection is None:
            # 直前にスロットを選択していた場合は、それを確定して実行する
            now_ms = int(time.monotonic() * 1000)
            if self._selection_latched is not None and now_ms <= int(self._selection_latch_until_ms):
                self.slotTriggered.emit(int(self._selection_latched.preset), int(self._selection_latched.slot))
                return
            self.step_preset(+1)
            return

        self.slotTriggered.emit(int(self._selection.preset), int(self._selection.slot))

    def step_preset(self, delta: int) -> None:
        """プリセットを切り替える（循環）。"""

        if not self._active:
            return
        d = int(delta)
        if d == 0:
            return
        cur = int(self._preset)
        nxt = ((cur - 1 + d) % 3) + 1
        if nxt != cur:
            self._preset = int(nxt)
            self._selection = None  # 切替時は選択をリセット（意図しない誤発火を避ける）
            self._selection_latched = None
            self._selection_latch_until_ms = 0
            self.presetChanged.emit(int(self._preset))
            self.set_action_feedback(f"Preset -> {self._preset}")
            self.update()

    def current_preset(self) -> int:
        return int(self._preset)

    def set_preset(self, preset: int) -> None:
        """外部入力によりプリセットを直接指定する。"""

        p = int(preset)
        if p not in (1, 2, 3):
            return
        if p == int(self._preset):
            return
        self._preset = int(p)
        self._selection = None
        self._selection_latched = None
        self._selection_latch_until_ms = 0
        self.presetChanged.emit(int(self._preset))
        self.update()

    def set_action_feedback(self, message: str) -> None:
        """直近の実行結果を短時間だけ中央に表示する（デバッグ用途）。"""

        self._action_msg = str(message)
        self._action_msg_until_ms = int(time.monotonic() * 1000) + 1200
        self.update()

    def _apply_active_state(self) -> None:
        # 表示中は入力を奪わないため、フォーカスは取らない
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # show() は呼び出し元で行う

    def _apply_inert_state(self) -> None:
        # 非表示中は確実に入力透過（万一showされても操作を邪魔しない）
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)

    def _move_to_screen_center_and_warp_center(self) -> None:
        """PieMenuを画面中央へ固定表示し、カーソルも中央へワープする。"""

        scr = QApplication.primaryScreen()
        geo = scr.availableGeometry() if scr is not None else None
        scx = int(geo.center().x()) if geo is not None else 0
        scy = int(geo.center().y()) if geo is not None else 0

        # ウィンドウ中心を画面中央へ合わせる
        w = int(self.width())
        h = int(self.height())
        x = int(scx - (w // 2))
        y = int(scy - (h // 2))
        self.move(x, y)

        cx = int(x + (w // 2))
        cy = int(y + (h // 2))
        self._center_screen_xy = (cx, cy)

        # 仮想中心を固定（カーソルを中央へワープ）
        try:
            pyautogui.moveTo(cx, cy)
        except Exception:
            pass

    def _preset_title(self) -> str:
        if int(self._preset) == 2:
            return "Preset 2 (Media)"
        if int(self._preset) == 3:
            return "Preset 3 (Custom)"
        return "Preset 1 (Custom)"

    def _slot_for(self, preset: int, slot: int) -> PieMenuSlot:
        s = self._store.get()
        if int(preset) == 3:
            return s.pie_menu.custom_3.slots[int(slot) - 1]
        # preset 2 は固定表示（ここではラベルのみ返す）
        if int(preset) == 2:
            ids = list(getattr(s.pie_menu, "preset2_layout"))
            act_id = str(ids[int(slot) - 1])
            act = media_actions_by_id()[act_id]
            return PieMenuSlot(label=act.label, type=act.type, value=act.value)
        return s.pie_menu.custom_1.slots[int(slot) - 1]

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if not self._active:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())
        cx = w / 2.0
        cy = h / 2.0
        radius = min(w, h) * 0.45
        inner_r = radius * 0.42

        # 背景（控えめな半透明）
        bg = QColor(20, 20, 20, 150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2))

        # スロット8分割
        outer_rect = QRectF(cx - radius, cy - radius, radius * 2.0, radius * 2.0)
        for i in range(8):
            slot = i + 1
            is_sel = bool(self._selection is not None and int(self._selection.slot) == int(slot))
            center_deg = float(i * 45.0)
            # 選択中は「扇形全体」を青でハイライトする
            if is_sel:
                fill = QColor(0, 140, 255, 110)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                # QtのdrawPieは 1/16度単位、0度は3時方向、正は反時計回り
                # スロット中心が上下左右/斜めに揃うよう、扇形を22.5°回転させる
                start_deg = float(center_deg - 22.5)
                span_deg = 45.0
                painter.drawPie(outer_rect, int(start_deg * 16), int(span_deg * 16))

            # 枠線（選択中は青、通常は薄白）
            draw_col = QColor(0, 140, 255, 220) if is_sel else QColor(255, 255, 255, 80)
            pen = QPen(draw_col)
            pen.setWidth(2 if is_sel else 1)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            # ラベル（セクタの中央）
            # 0/90/180/270 が上下左右になるよう中心角をそのまま使う
            mid = math.radians(center_deg)
            tx = cx + math.cos(mid) * (radius * 0.72)
            ty = cy - math.sin(mid) * (radius * 0.72)

            slot_cfg = self._slot_for(self._preset, slot)
            text = str(slot_cfg.label or f"Slot {slot}")

            painter.setPen(QColor(255, 255, 255, 220))
            f = QFont()
            f.setPointSize(10 if not is_sel else 11)
            f.setBold(bool(is_sel))
            painter.setFont(f)
            painter.drawText(int(tx - 48), int(ty - 12), 96, 24, int(Qt.AlignmentFlag.AlignCenter), text)

        # セクタ境界線（中心から外周へ）
        # 境界を 22.5° ずらし、中心が上下左右/斜めに揃うようにする
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(8):
            ang_deg = float(k * 45.0 + 22.5)
            ang = math.radians(ang_deg)
            x2 = cx + math.cos(ang) * radius
            y2 = cy - math.sin(ang) * radius
            painter.drawLine(int(cx), int(cy), int(x2), int(y2))

        # 内円（中央表示領域）
        painter.setPen(QPen(QColor(255, 255, 255, 140), 2))
        painter.setBrush(QColor(0, 0, 0, 120))
        painter.drawEllipse(int(cx - inner_r), int(cy - inner_r), int(inner_r * 2), int(inner_r * 2))

        painter.setPen(QColor(255, 255, 255, 235))
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(
            int(cx - inner_r),
            int(cy - 18),
            int(inner_r * 2),
            22,
            int(Qt.AlignmentFlag.AlignCenter),
            self._preset_title(),
        )

        painter.setPen(QColor(255, 255, 255, 180))
        f2 = QFont()
        f2.setPointSize(10)
        f2.setBold(False)
        painter.setFont(f2)
        sub = "利き手: スロット=実行 / 中央=Preset切替"
        painter.drawText(
            int(cx - inner_r),
            int(cy + 2),
            int(inner_r * 2),
            36,
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            sub,
        )

        # 直近の実行フィードバック（短時間）
        now_ms = int(time.monotonic() * 1000)
        if self._action_msg and now_ms <= int(self._action_msg_until_ms):
            painter.setPen(QColor(0, 200, 120, 230))
            f3 = QFont()
            f3.setPointSize(11)
            f3.setBold(True)
            painter.setFont(f3)
            painter.drawText(
                int(cx - inner_r),
                int(cy + 38),
                int(inner_r * 2),
                24,
                int(Qt.AlignmentFlag.AlignCenter),
                self._action_msg,
            )

        # クリック受信の簡易インジケータ（OK/NGとは別）
        if now_ms <= int(self._last_click_until_ms):
            painter.setPen(QColor(0, 140, 255, 230))
            f4 = QFont()
            f4.setPointSize(11)
            f4.setBold(True)
            painter.setFont(f4)
            painter.drawText(
                int(cx - inner_r),
                int(cy + 62),
                int(inner_r * 2),
                20,
                int(Qt.AlignmentFlag.AlignCenter),
                "CLICK",
            )

