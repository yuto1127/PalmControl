from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pyautogui
from PyQt6.QtCore import Qt, QTimer, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QFileIconProvider, QWidget
from PyQt6.QtCore import QFileInfo

from src.utils.config_loader import ConfigStore, PieMenuSlot
from src.utils.macos_overlay import apply_macos_overlay_hints, hide_panel_overlay, is_macos_overlay_available, show_panel_overlay
from src.core.media_preset import media_actions_by_id


def _overlay_debug_enabled() -> bool:
    # PieMenu はフレーム毎に呼ばれ得るため、別フラグで明示的にONにしたときだけ出す。
    v = str(os.environ.get("PALMCONTROL_PIE_DEBUG", "")).strip().lower()
    return v in ("1", "true", "yes", "on", "debug")


def _overlay_dbg(msg: str) -> None:
    if not _overlay_debug_enabled():
        return
    try:
        import sys

        print(f"[PalmControl][pie_menu] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _selection_arm_duration_ms() -> int:
    """PieMenu 表示直後はノイズで右セクタ等に入りやすいため、この時間は原点を手に追従させスロットを確定しない。"""

    try:
        v = int(os.environ.get("PALMCONTROL_PIE_ARM_MS", "220"))
        return max(0, min(v, 1200))
    except Exception:
        return 220


def _pie_warp_cursor_enabled() -> bool:
    """表示開始時に OS カーソルを PieMenu 中央へ移す（既定 ON）。0/false/off で無効。"""

    v = str(os.environ.get("PALMCONTROL_PIE_WARP_CURSOR", "1")).strip().lower()
    return v not in ("0", "false", "no", "off")


def _pie_vector_gain() -> float:
    """PieMenu のスライス選択感度（相対ベクトルの増幅率）。"""

    try:
        v = float(os.environ.get("PALMCONTROL_PIE_GAIN", "1.6"))
        return float(max(0.4, min(v, 12.0)))
    except Exception:
        return 1.6


def _pie_invert_y() -> bool:
    """環境差で上下が反転するケース向けの補正（既定OFF）。"""

    v = str(os.environ.get("PALMCONTROL_PIE_INVERT_Y", "0")).strip().lower()
    return v not in ("0", "false", "no", "off")


def _pie_rotation_deg() -> float:
    """角度の最終補正（度）。90/180 などで直感に合わせられる。"""

    try:
        return float(os.environ.get("PALMCONTROL_PIE_ROT_DEG", "0"))
    except Exception:
        return 0.0


def _pie_pointer_y_axis() -> str:
    """pointer_xy の Y 軸の向き。

    - down: 下が+（MediaPipe/画像座標系の一般的な向き）
    - up: 上が+（どこかで反転済みの入力）
    """

    v = str(os.environ.get("PALMCONTROL_PIE_Y_AXIS", "up")).strip().lower()
    return "down" if v in ("down", "img", "image", "0") else "up"


def _pie_smoothing_alpha() -> float:
    """相対ベクトルの平滑化（EMA）の係数。大きいほど反応が速いがブレやすい。"""

    try:
        v = float(os.environ.get("PALMCONTROL_PIE_SMOOTH_ALPHA", "0.45"))
        return float(max(0.05, min(v, 1.0)))
    except Exception:
        return 0.45


def _pie_deadzone_enter() -> float:
    """中央デッドゾーン（入る側）。小さくすると反応が速いが揺れやすい。"""

    try:
        v = float(os.environ.get("PALMCONTROL_PIE_DEADZONE", "0.050"))
        return float(max(0.01, min(v, 0.20)))
    except Exception:
        return 0.050


def _pie_boundary_hysteresis_deg() -> float:
    """セクタ境界付近のチラつきを抑える角度マージン（度）。"""

    try:
        v = float(os.environ.get("PALMCONTROL_PIE_BOUNDARY_DEG", "6.0"))
        return float(max(0.0, min(v, 18.0)))
    except Exception:
        return 6.0


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
    - 表示開始時（既定）: OS カーソルをメニュー中央へ移し、画面操作の基準を中央に合わせる。
    - スロット選択はカメラ上の利き手 pointer_xy について、アーム期間終了後に固定した原点からの
      相対変位（ベクトル）で角度を決める（マウス座標は選択計算に使わない）。
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
        self._last_front_refresh_ms: int = 0
        self._selection_arm_until_ms: int = 0
        self._vec_ema: Tuple[float, float] = (0.0, 0.0)

        # applicationスロットのアイコンを描画するためのキャッシュ
        self._icon_provider = QFileIconProvider()
        self._icon_cache: Dict[str, QPixmap] = {}

        self._macos_overlay_ok: bool = bool(is_macos_overlay_available())

        self._init_window()
        self._apply_inert_state()

    def _icon_pixmap_for_slot(self, slot_cfg: PieMenuSlot, *, size: int) -> Optional[QPixmap]:
        """slot_cfg が application のとき、OSのファイルアイコンPixmapを返す。"""

        try:
            if str(slot_cfg.type).strip().lower() != "application":
                return None
            path = str(slot_cfg.value or "").strip()
            if not path:
                return None
            if path in self._icon_cache:
                return self._icon_cache[path]

            fi = QFileInfo(path)
            if not fi.exists():
                return None

            icon = self._icon_provider.icon(fi)
            if icon.isNull():
                return None
            pm = icon.pixmap(QSize(int(size), int(size)))
            if pm.isNull():
                return None
            self._icon_cache[path] = pm
            return pm
        except Exception:
            return None

    def _init_window(self) -> None:
        self.setWindowTitle("PieMenu")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            # macOS のネイティブ全画面では ToolTip が描画されない/潜るケースがあるため Tool を使う。
            # WA_ShowWithoutActivating によりフォーカスを奪いにくくする。
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # サイズは固定（描画を単純にする）。必要なら後で設定化できる。
        self.resize(520, 520)
        # NOTE:
        # ここ（初期化直後）は NSWindow/NSView の生成タイミング差が大きく、
        # PyObjC 経由の操作で環境によってはクラッシュし得る。
        # macOS 向けのヒントは「表示開始時（set_active）」に遅延適用する。

    def is_active(self) -> bool:
        return bool(self._active)

    def _native_panel_env_enabled(self) -> bool:
        v = str(os.environ.get("PALMCONTROL_PANEL_OVERLAY", "")).strip().lower()
        return v in ("1", "true", "on", "yes")

    def _use_native_pie_visual(self) -> bool:
        """Qt ウィンドウが全画面動画の下に隠れる環境向けに、NSPanel 上へ円形メニューを描画する。"""

        return sys.platform == "darwin" and bool(is_macos_overlay_available()) and self._native_panel_env_enabled()

    def _sync_native_panel(self) -> None:
        if not self._active or not self._native_panel_env_enabled():
            return
        now_ms = int(time.monotonic() * 1000)
        labels: list[str] = []
        icon_paths: list[Optional[str]] = []
        for slot in range(1, 9):
            cfg = self._slot_for(self._preset, slot)
            labels.append(str(cfg.label or f"Slot {slot}")[:48])
            ip: Optional[str] = None
            if str(cfg.type).strip().lower() == "application":
                p = str(cfg.value or "").strip()
                if p:
                    fi = QFileInfo(p)
                    if fi.exists():
                        c = fi.canonicalFilePath()
                        ip = str(c) if c else str(fi.absoluteFilePath())
            icon_paths.append(ip)
        sel = int(self._selection.slot) if self._selection is not None else None
        action = ""
        if self._action_msg and now_ms <= int(self._action_msg_until_ms):
            action = str(self._action_msg)
        click = now_ms <= int(self._last_click_until_ms)
        geo = self.frameGeometry()
        show_panel_overlay(
            int(geo.x()),
            int(geo.y()),
            int(geo.width()),
            int(geo.height()),
            pie_state={
                "labels": labels,
                "icon_paths": icon_paths,
                "preset_title": self._preset_title(),
                "selection_slot": sel,
                "subtitle": "利き手: スロット=実行 / 中央=Preset切替",
                "action_msg": action,
                "show_click": click,
            },
        )

    def _request_pie_redraw(self) -> None:
        if self._use_native_pie_visual():
            self._sync_native_panel()
        else:
            self.update()

    def set_active(self, active: bool, *, pointer_xy: Optional[Tuple[float, float]] = None) -> None:
        """表示/非表示を切り替える。

        active=True になった瞬間:
        - メニューを画面中央へ配置（既定で OS カーソルもその中央へ移動）
        - 直後のアーム期間中は手の位置に原点を追従させ、その後は原点固定で相対移動からスロットを決める
        """

        active = bool(active)
        _overlay_dbg(f"set_active(active={active}) prev={self._active} pointer_xy={'yes' if pointer_xy is not None else 'no'}")
        if active == self._active:
            # 既に表示中なら origin は固定したまま pointer だけ更新
            if active:
                self.update_pointer(pointer_xy)
                self._refresh_frontmost_if_needed()
            return

        self._active = active
        if self._active:
            self._pointer_origin_xy = pointer_xy
            self._pointer_xy = pointer_xy
            self._selection = None
            self._selection_latched = None
            self._selection_latch_until_ms = 0
            arm = int(_selection_arm_duration_ms())
            self._selection_arm_until_ms = int(time.monotonic() * 1000) + arm if arm > 0 else 0
            self._move_to_screen_center_and_warp_center()
            self._apply_active_state()
            if self._use_native_pie_visual():
                _overlay_dbg("native NSPanel pie (PALMCONTROL_PANEL_OVERLAY); Qt window stays hidden")
                self._sync_native_panel()
                QTimer.singleShot(0, self._sync_native_panel)
                QTimer.singleShot(50, self._sync_native_panel)
                QTimer.singleShot(200, self._sync_native_panel)
            else:
                self.show()
                _overlay_dbg("show() called; applying macos overlay hints")
                self._force_frontmost()
                QTimer.singleShot(0, self._force_frontmost)
                QTimer.singleShot(50, self._force_frontmost)
                QTimer.singleShot(200, self._force_frontmost)
                try:
                    if self._native_panel_env_enabled():
                        geo = self.frameGeometry()
                        _overlay_dbg("PALMCONTROL_PANEL_OVERLAY=1 (no PyObjC pie view) -> banner show_panel_overlay()")
                        show_panel_overlay(int(geo.x()), int(geo.y()), int(geo.width()), int(geo.height()))
                except Exception:
                    pass
            if self._native_panel_env_enabled() and not self._macos_overlay_ok:
                self.set_action_feedback("macOS: pyobjc-framework-Cocoa 未導入のため全画面に表示できない可能性があります")
            # raise_() は環境によってアプリが前面化し、操作対象のアプリからフォーカスが奪われることがあるため避ける
        else:
            self._pointer_origin_xy = None
            self._pointer_xy = None
            self._selection = None
            self._selection_latched = None
            self._selection_latch_until_ms = 0
            self._selection_arm_until_ms = 0
            self._apply_inert_state()
            self.hide()
            try:
                hide_panel_overlay()
            except Exception:
                pass

        self._request_pie_redraw()

    def _refresh_frontmost_if_needed(self) -> None:
        """表示中も定期的に前面化を再適用する。

        動画プレイヤーが後から全画面レイヤーを作ると、初回の orderFront だけでは下に潜ることがある。
        """

        now_ms = int(time.monotonic() * 1000)
        if (now_ms - int(self._last_front_refresh_ms)) < 300:
            return
        self._last_front_refresh_ms = int(now_ms)
        if self._use_native_pie_visual():
            self._sync_native_panel()
        else:
            self._force_frontmost()

    def _force_frontmost(self) -> None:
        if not self._active:
            return
        if self._use_native_pie_visual():
            self._sync_native_panel()
            return
        try:
            self.show()
        except Exception:
            pass
        try:
            apply_macos_overlay_hints(self)
        except Exception:
            pass
        try:
            if self._native_panel_env_enabled():
                geo = self.frameGeometry()
                show_panel_overlay(int(geo.x()), int(geo.y()), int(geo.width()), int(geo.height()))
        except Exception:
            pass

    def update_pointer(self, pointer_xy: Optional[Tuple[float, float]], *, contact: bool = False) -> None:
        """利き手の pointer_xy を受け取り、固定原点からの相対移動量でスロット選択を更新する。"""

        if not self._active:
            return
        self._pointer_xy = pointer_xy

        now_ms = int(time.monotonic() * 1000)
        latch_valid = bool(self._selection_latched is not None and now_ms <= int(self._selection_latch_until_ms))

        if pointer_xy is None:
            # クリック姿勢などでpointer_xyが一時的に欠けることがあるため、短時間は直前選択を保持する。
            self._selection = self._selection_latched if latch_valid else None
            self._request_pie_redraw()
            return

        # ピンチ（クリック）中は指形状が変わりやすく、選択がズレやすいのでスロットを凍結する
        if bool(contact):
            self._selection = self._selection_latched if latch_valid else self._selection
            self._request_pie_redraw()
            return

        if now_ms < int(self._selection_arm_until_ms):
            # 開幕直後: 原点を毎フレーム手に合わせ、相対変位ゼロ付近からスライス選択を開始する
            self._pointer_origin_xy = pointer_xy
            self._selection = None
            self._selection_latched = None
            self._selection_latch_until_ms = 0
            self._vec_ema = (0.0, 0.0)
            self._request_pie_redraw()
            return

        if self._pointer_origin_xy is None:
            self._pointer_origin_xy = pointer_xy
            self._selection = None
            self._request_pie_redraw()
            return

        dx0 = float(pointer_xy[0]) - float(self._pointer_origin_xy[0])
        dy0 = float(pointer_xy[1]) - float(self._pointer_origin_xy[1])
        # Y軸の向きは環境差が出るため、入力(pointer_xy)の向きに応じて数学座標（上が+）へ正規化する。
        # ここで正規化しておくと、描画（0°=右、+90°=上）と選択判定が一致する。
        gain = float(_pie_vector_gain())
        dx = float(dx0) * gain
        if _pie_pointer_y_axis() == "down":
            dy = float(-dy0) * gain
        else:
            dy = float(dy0) * gain
        if _pie_invert_y():
            dy = -float(dy)

        # 反応性と安定性の両立: 相対ベクトルを軽く平滑化し、デッドゾーンは「入る/出る」でヒステリシスを付ける
        a = float(_pie_smoothing_alpha())
        ex, ey = self._vec_ema
        ex = (a * float(dx)) + ((1.0 - a) * float(ex))
        ey = (a * float(dy)) + ((1.0 - a) * float(ey))
        self._vec_ema = (float(ex), float(ey))

        r = math.sqrt(ex * ex + ey * ey)
        dz_in = float(_pie_deadzone_enter())
        dz_out = float(max(0.005, dz_in * 0.72))
        if self._selection is None:
            if r < dz_in:
                self._request_pie_redraw()
                return
        else:
            if r < dz_out:
                self._selection = None
                self._request_pie_redraw()
                return

        # 角度（右=0、上=+90、左=180、下=-90）
        ang = math.degrees(math.atan2(ey, ex)) + float(_pie_rotation_deg())
        # 8分割: 右を1番として時計回りに 1..8
        # セクタ境界を中央に寄せるため、22.5度オフセットを入れる
        frac = ((ang + 360.0 + 22.5) % 360.0) / 45.0
        idx0 = int(frac // 1.0)  # 0..7
        slot = int(idx0 + 1)

        # 境界付近でチラつく場合は、直前スロットを維持（ただし大きく振ったときは追従）
        if self._selection is not None and int(self._selection.slot) != int(slot):
            margin = float(_pie_boundary_hysteresis_deg())
            f = float(frac - math.floor(frac))
            dist_deg = float(min(f, 1.0 - f) * 45.0)
            if dist_deg < margin and r < float(dz_in * 3.2):
                slot = int(self._selection.slot)
        self._selection = PieMenuSelection(preset=int(self._preset), slot=int(slot))
        # 選択更新が来たらラッチを更新（クリック姿勢移行で選択が消えないようにする）
        self._selection_latched = self._selection
        self._selection_latch_until_ms = int(now_ms + 700)
        self._request_pie_redraw()

    def handle_click(self, *, right: bool = False) -> None:
        """クリックイベントを受け取り、スロット実行/中央クリック処理を行う。"""

        if not self._active:
            return
        # 右クリックは現状「同じ実行」扱い（将来別機能にできるよう引数は残す）
        _ = bool(right)

        # デバッグ: クリックが来たこと自体を可視化する
        self._last_click_until_ms = int(time.monotonic() * 1000) + 350
        self._request_pie_redraw()

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
            self._request_pie_redraw()

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
        self._request_pie_redraw()

    def set_action_feedback(self, message: str) -> None:
        """直近の実行結果を短時間だけ中央に表示する（デバッグ用途）。"""

        self._action_msg = str(message)
        self._action_msg_until_ms = int(time.monotonic() * 1000) + 1200
        self._request_pie_redraw()

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
        """PieMenu を画面中央へ置き、既定では OS カーソルをその中央へ移動する。"""

        scr = QGuiApplication.screenAt(QCursor.pos())
        if scr is None:
            scr = QApplication.primaryScreen()
        # 全画面アプリ上では availableGeometry が狭くなる/不正になることがあるため geometry を優先する
        geo = scr.geometry() if scr is not None else None
        scx = int(geo.center().x()) if geo is not None else 0
        scy = int(geo.center().y()) if geo is not None else 0

        # ウィンドウ中心を画面中央へ合わせる
        w = int(self.width())
        h = int(self.height())
        x = int(scx - (w // 2))
        y = int(scy - (h // 2))
        # 画面外にはみ出して欠けるのを防ぐ（全画面/マルチモニタ/スケール差対策）
        if geo is not None:
            min_x = int(geo.left())
            min_y = int(geo.top())
            max_x = int(geo.right() - w + 1)
            max_y = int(geo.bottom() - h + 1)
            x = max(min_x, min(int(x), max_x))
            y = max(min_y, min(int(y), max_y))
        self.move(x, y)

        cx = int(x + (w // 2))
        cy = int(y + (h // 2))
        self._center_screen_xy = (cx, cy)

        if _pie_warp_cursor_enabled():
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
        if self._use_native_pie_visual():
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

            # applicationスロットはアイコンを併記して識別しやすくする
            # 小さすぎると識別しにくいので、やや大きめに描く
            icon_size = 38 if is_sel else 34
            pm = self._icon_pixmap_for_slot(slot_cfg, size=icon_size)
            if pm is not None and (not pm.isNull()):
                # アイコンを上、ラベルを下に配置して読みやすくする
                painter.drawPixmap(int(tx - (icon_size // 2)), int(ty - 40), pm)
                painter.drawText(int(tx - 72), int(ty + 2), 144, 28, int(Qt.AlignmentFlag.AlignCenter), text)
            else:
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

