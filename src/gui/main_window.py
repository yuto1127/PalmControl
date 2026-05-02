from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional, Tuple

import sys

from PyQt6.QtCore import QEvent, QLocale, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QImage, QKeyEvent, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMainWindow,
)

from src.gui.worker import VisionControlWorker
from src.utils.config_loader import ConfigStore
from src.core.media_preset import MEDIA_ACTIONS

# (YAML dotted path, ウィジェット, カメラ再初期化が必要か)
SettingsBinding = Tuple[str, QWidget, bool]
PieBinding = Tuple[str, QWidget]


class _AspectFitPixmapLabel(QWidget):
    """カメラプレビュー用: QLabelのPixmapスケーリングだと余白/クリップが分かりにくいので自前で中央フィット描画する。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._src: Optional[QPixmap] = None
        self._scaled: Optional[QPixmap] = None

    def set_source_pixmap(self, pix: Optional[QPixmap]) -> None:
        self._src = pix
        self._scaled = None
        self.update()

    def set_scaled_pixmap(self, pix: Optional[QPixmap]) -> None:
        """外部（ScrollArea幅基準）でスケール済みPixmapを渡して描画する。"""
        self._scaled = pix
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())

        pix = self._scaled if self._scaled is not None else self._src
        if pix is None or pix.isNull():
            return

        target = self.rect()
        if target.width() <= 1 or target.height() <= 1:
            return

        x = (target.width() - pix.width()) // 2
        y = (target.height() - pix.height()) // 2
        painter.drawPixmap(x, y, pix)


def _format_shortcut_from_key_event(ev: QKeyEvent) -> Optional[str]:
    """Qtのキーイベントから 'ctrl+shift+p' / 'command+c'（PyAutoGUI想定）形式へ整形する。"""

    key = int(ev.key())
    if key in (
        int(Qt.Key.Key_Control),
        int(Qt.Key.Key_Shift),
        int(Qt.Key.Key_Alt),
        int(Qt.Key.Key_Meta),
    ):
        return None

    # 修飾キーの順序（イベント単体では macOS の物理 Ctrl が欠けることがあるのでキーボード状態も併用）
    mods: List[str] = []
    m = ev.modifiers()
    try:
        app = QGuiApplication.instance()
        if app is not None:
            m |= app.keyboardModifiers()
    except Exception:
        pass

    # macOS: Qt は Command を ControlModifier、物理 Ctrl を MetaModifier として扱うことが多い。PyAutoGUI では command が正式名。
    if sys.platform.startswith("darwin"):
        if bool(m & Qt.KeyboardModifier.MetaModifier):
            mods.append("ctrl")
        if bool(m & Qt.KeyboardModifier.ControlModifier):
            mods.append("command")
    else:
        if bool(m & Qt.KeyboardModifier.MetaModifier):
            mods.append("win")
        if bool(m & Qt.KeyboardModifier.ControlModifier):
            mods.append("ctrl")
    if bool(m & Qt.KeyboardModifier.AltModifier):
        mods.append("alt")
    if bool(m & Qt.KeyboardModifier.ShiftModifier):
        mods.append("shift")

    # 文字キーは text 優先（レイアウト依存のため）
    # NOTE: strip() するとスペースが消えるので使わない
    t = ev.text() or ""

    # Ctrl+英字は text が制御文字（^C=\x03）になる。修飾子ビットが欠けてもここで復元できる。
    if len(t) == 1:
        oc = ord(t)
        if 1 <= oc <= 26:
            letter = chr(ord("a") + oc - 1)
            if "ctrl" not in mods:
                if sys.platform.startswith("darwin") and "command" not in mods:
                    mods.insert(0, "ctrl")
                elif bool(m & Qt.KeyboardModifier.ControlModifier):
                    mods.insert(0, "ctrl")
            return "+".join([*mods, letter])

    if t == " ":
        main = "space"
    elif t and len(t) == 1 and t.isprintable() and t not in ("\r", "\n", "\t"):
        main = t.lower()
    else:
        # 特殊キー
        special = {
            int(Qt.Key.Key_Space): "space",
            int(Qt.Key.Key_Return): "enter",
            int(Qt.Key.Key_Enter): "enter",
            int(Qt.Key.Key_Escape): "esc",
            int(Qt.Key.Key_Tab): "tab",
            int(Qt.Key.Key_Backspace): "backspace",
            int(Qt.Key.Key_Delete): "delete",
            int(Qt.Key.Key_Left): "left",
            int(Qt.Key.Key_Right): "right",
            int(Qt.Key.Key_Up): "up",
            int(Qt.Key.Key_Down): "down",
            int(Qt.Key.Key_Home): "home",
            int(Qt.Key.Key_End): "end",
            int(Qt.Key.Key_PageUp): "pageup",
            int(Qt.Key.Key_PageDown): "pagedown",
        }
        if key in special:
            main = special[key]
        elif int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F24):
            main = f"f{key - int(Qt.Key.Key_F1) + 1}"
        elif int(Qt.Key.Key_0) <= key <= int(Qt.Key.Key_9):
            main = str(key - int(Qt.Key.Key_0))
        else:
            # 記号キー（US配列相当の名称へ寄せる）
            punct = {
                int(Qt.Key.Key_Minus): "-",
                int(Qt.Key.Key_Equal): "=",
                int(Qt.Key.Key_BracketLeft): "[",
                int(Qt.Key.Key_BracketRight): "]",
                int(Qt.Key.Key_Backslash): "\\",
                int(Qt.Key.Key_Semicolon): ";",
                int(Qt.Key.Key_Apostrophe): "'",
                int(Qt.Key.Key_Comma): ",",
                int(Qt.Key.Key_Period): ".",
                int(Qt.Key.Key_Slash): "/",
                int(Qt.Key.Key_QuoteLeft): "`",
            }
            if key in punct:
                main = punct[key]
            else:
                return None

    parts = [*mods, main] if main else mods
    if not parts:
        return None
    return "+".join(parts)


class ShortcutValueLineEdit(QLineEdit):
    """Pie の Shortcut Value 用: macOS の Cmd 系が OS/Qt に食われにくいよう直接キャプチャする。"""

    def __init__(self, type_combo: QComboBox, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._typ = type_combo
        try:
            self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        except Exception:
            pass

    def event(self, e: QEvent) -> bool:  # type: ignore[override]
        # Qt がショートカットとして先に処理する前に拾う（特に macOS の Meta 組み合わせ）
        try:
            if e.type() == QEvent.Type.ShortcutOverride and str(self._typ.currentData()) == "shortcut":
                if hasattr(e, "key") and hasattr(e, "modifiers"):
                    keyev = e  # QKeyEvent（ShortcutOverride）
                    key = int(keyev.key())
                    if key in (int(Qt.Key.Key_Backspace), int(Qt.Key.Key_Delete)):
                        self.setText("")
                        e.accept()
                        return True
                    s = _format_shortcut_from_key_event(keyev)  # type: ignore[arg-type]
                    if s:
                        self.setText(s)
                        e.accept()
                        return True
        except Exception:
            pass
        return super().event(e)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if str(self._typ.currentData()) != "shortcut":
            super().keyPressEvent(event)
            return

        key = int(event.key())
        if key in (int(Qt.Key.Key_Backspace), int(Qt.Key.Key_Delete)):
            self.setText("")
            event.accept()
            return

        s = _format_shortcut_from_key_event(event)
        if s:
            self.setText(s)
            event.accept()
            return

        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    """PalmControl GUIメインウィンドウ（研究用）。"""

    previewEnabledChanged = pyqtSignal(bool)
    controlEnabledChanged = pyqtSignal(bool)
    requestRestartCamera = pyqtSignal()
    exitRequested = pyqtSignal()

    def __init__(self, store: ConfigStore, worker: VisionControlWorker) -> None:
        super().__init__()
        self._store = store
        self._worker = worker
        self._motion_last_qimage: Optional[QImage] = None

        self._settings_bindings: List[SettingsBinding] = []
        self._settings_edit_mode = False
        self._pie_bindings: List[PieBinding] = []
        self._pie_preset2_combos: List[QComboBox] = []
        self._pie_extra_buttons: List[QPushButton] = []
        self._pie_edit_mode = False
        self._prev_tab_index = 0
        self._log_dir_label: Optional[QLabel] = None
        self._log_file_label: Optional[QLabel] = None

        self.setWindowTitle("PalmControl")
        self.resize(980, 720)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self.setCentralWidget(self._tabs)

        self._settings_tab = self._build_settings_tab()
        self._pie_menu_tab = self._build_pie_menu_tab()
        self._motion_tab = self._build_motion_tab()
        self._log_tab = self._build_log_tab()
        self._manual_tab = self._build_manual_tab()

        self._tabs.addTab(self._settings_tab, "設定")
        self._tabs.addTab(self._pie_menu_tab, "PieMenu設定")
        self._tabs.addTab(self._motion_tab, "モーションテスト")
        self._tabs.addTab(self._log_tab, "ログ")
        self._tabs.addTab(self._manual_tab, "マニュアル")

        self._tabs.currentChanged.connect(self._on_tab_changed)

        # Worker signals
        self._worker.frameReady.connect(self._on_frame_ready)
        self._worker.statusReady.connect(self._on_status_ready)
        self._worker.error.connect(self._on_worker_error)

        # GUI → Worker signals
        self.previewEnabledChanged.connect(self._worker.setPreviewEnabled)
        self.controlEnabledChanged.connect(self._worker.setControlEnabled)
        self.requestRestartCamera.connect(self._worker.requestRestartCamera)

        self._on_tab_changed(self._tabs.currentIndex())

    def _bind_setting(self, path: str, w: QWidget, *, camera_restart: bool = False) -> None:
        self._settings_bindings.append((path, w, camera_restart))

    def _bind_pie(self, path: str, w: QWidget) -> None:
        self._pie_bindings.append((path, w))

    def _get_raw_setting_value(self, dotted_path: str) -> Any:
        parts = dotted_path.split(".")
        cur: Any = self._store.as_dict()
        for p in parts:
            if not isinstance(cur, dict):
                raise KeyError(dotted_path)
            nk: Any = None
            if p in cur:
                nk = p
            elif p.isdigit():
                for cand in (int(p), str(int(p))):
                    if cand in cur:
                        nk = cand
                        break
            if nk is None:
                raise KeyError(dotted_path)
            cur = cur[nk]
        return cur

    def _set_widget_from_value(self, w: QWidget, val: Any) -> None:
        w.blockSignals(True)
        try:
            if isinstance(w, QSpinBox):
                w.setValue(int(val))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(float(val))
            elif isinstance(w, QComboBox):
                idx = w.findData(str(val))
                if idx >= 0:
                    w.setCurrentIndex(idx)
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(val))
            elif isinstance(w, QLineEdit):
                w.setText(str(val))
        finally:
            w.blockSignals(False)

    @staticmethod
    def _tune_double_spin(w: QDoubleSpinBox) -> None:
        """小数入力をしやすくする共通設定。

        QDoubleSpinBox は入力途中の状態で補正が走ると「桁を消して打ち直す」操作がしづらい。
        - keyboardTracking=False: Enter/フォーカスアウトまで値を確定しない
        - correction: 不正状態は確定時に前値へ戻す（入力中は邪魔しない）
        """

        try:
            # 小数点は常に '.' を許可（環境ロケール依存で ',' になって入力が弾かれるのを防ぐ）
            w.setLocale(QLocale.c())
            try:
                w.setGroupSeparatorShown(False)
            except Exception:
                pass
            w.setKeyboardTracking(False)
            w.setCorrectionMode(QAbstractSpinBox.CorrectionMode.CorrectToPreviousValue)
        except Exception:
            pass

    @staticmethod
    def _get_widget_value(w: QWidget) -> Any:
        if isinstance(w, QSpinBox):
            return int(w.value())
        if isinstance(w, QDoubleSpinBox):
            return float(w.value())
        if isinstance(w, QComboBox):
            return str(w.currentData())
        if isinstance(w, QCheckBox):
            return bool(w.isChecked())
        if isinstance(w, QLineEdit):
            return str(w.text())
        raise TypeError(type(w))

    def _reload_settings_widgets_from_store(self) -> None:
        for path, w, _ in self._settings_bindings:
            try:
                val = self._get_raw_setting_value(path)
                self._set_widget_from_value(w, val)
            except Exception:
                pass
        try:
            s = self._store.get()
            if self._log_dir_label is not None:
                self._log_dir_label.setText(str(s.logging.log_dir))
            if self._log_file_label is not None:
                self._log_file_label.setText(str(s.logging.log_file_name))
        except Exception:
            pass

    def _commit_settings_from_widgets(self) -> None:
        need_restart = False
        for path, w, cam_r in self._settings_bindings:
            try:
                val = self._get_widget_value(w)
                self._set_value(path, val)
                if cam_r:
                    need_restart = True
            except Exception:
                pass
        if need_restart:
            self.requestRestartCamera.emit()

    def _set_settings_fields_enabled(self, enabled: bool) -> None:
        for _, w, __ in self._settings_bindings:
            w.setEnabled(enabled)

    def _on_settings_edit_clicked(self) -> None:
        self._settings_edit_mode = True
        self._set_settings_fields_enabled(True)
        self._btn_settings_edit.setVisible(False)
        self._btn_settings_apply.setVisible(True)
        self._btn_settings_cancel.setVisible(True)

    def _on_settings_apply_clicked(self) -> None:
        self._commit_settings_from_widgets()
        self._settings_edit_mode = False
        self._set_settings_fields_enabled(False)
        self._btn_settings_edit.setVisible(True)
        self._btn_settings_apply.setVisible(False)
        self._btn_settings_cancel.setVisible(False)

    def _on_settings_cancel_clicked(self) -> None:
        self._reload_settings_widgets_from_store()
        self._settings_edit_mode = False
        self._set_settings_fields_enabled(False)
        self._btn_settings_edit.setVisible(True)
        self._btn_settings_apply.setVisible(False)
        self._btn_settings_cancel.setVisible(False)

    def _reload_pie_widgets_from_store(self) -> None:
        s = self._store.get()
        for i, cb in enumerate(self._pie_preset2_combos):
            if i < len(s.pie_menu.preset2_layout):
                cur = str(s.pie_menu.preset2_layout[i])
                idx = cb.findData(cur)
                cb.blockSignals(True)
                try:
                    cb.setCurrentIndex(idx if idx >= 0 else 0)
                finally:
                    cb.blockSignals(False)
        for path, w in self._pie_bindings:
            try:
                val = self._get_raw_setting_value(path)
                self._set_widget_from_value(w, val)
            except Exception:
                pass

    def _commit_pie_from_widgets(self) -> None:
        try:
            ids = [str(cb.currentData()) for cb in self._pie_preset2_combos]
            self._set_value("pie_menu.preset2_layout", ids)
        except Exception:
            pass
        for path, w in self._pie_bindings:
            try:
                self._set_value(path, self._get_widget_value(w))
            except Exception:
                pass

    def _set_pie_fields_enabled(self, enabled: bool) -> None:
        for _, w in self._pie_bindings:
            w.setEnabled(enabled)
        for cb in self._pie_preset2_combos:
            cb.setEnabled(enabled)
        for b in self._pie_extra_buttons:
            b.setEnabled(enabled)

    def _on_pie_edit_clicked(self) -> None:
        self._pie_edit_mode = True
        self._set_pie_fields_enabled(True)
        self._btn_pie_edit.setVisible(False)
        self._btn_pie_apply.setVisible(True)
        self._btn_pie_cancel.setVisible(True)

    def _on_pie_apply_clicked(self) -> None:
        self._commit_pie_from_widgets()
        self._pie_edit_mode = False
        self._set_pie_fields_enabled(False)
        self._btn_pie_edit.setVisible(True)
        self._btn_pie_apply.setVisible(False)
        self._btn_pie_cancel.setVisible(False)

    def _on_pie_cancel_clicked(self) -> None:
        self._reload_pie_widgets_from_store()
        self._pie_edit_mode = False
        self._set_pie_fields_enabled(False)
        self._btn_pie_edit.setVisible(True)
        self._btn_pie_apply.setVisible(False)
        self._btn_pie_cancel.setVisible(False)

    # -----------------------------
    # Settings tab
    # -----------------------------
    def _build_settings_tab(self) -> QWidget:
        self._settings_bindings.clear()

        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        edit_row = QHBoxLayout()
        self._btn_settings_edit = QPushButton("設定を編集")
        self._btn_settings_apply = QPushButton("変更を適用")
        self._btn_settings_cancel = QPushButton("キャンセル")
        self._btn_settings_apply.setVisible(False)
        self._btn_settings_cancel.setVisible(False)
        self._btn_settings_edit.clicked.connect(self._on_settings_edit_clicked)
        self._btn_settings_apply.clicked.connect(self._on_settings_apply_clicked)
        self._btn_settings_cancel.clicked.connect(self._on_settings_cancel_clicked)
        edit_row.addWidget(self._btn_settings_edit)
        edit_row.addWidget(self._btn_settings_apply)
        edit_row.addWidget(self._btn_settings_cancel)
        edit_row.addStretch(1)
        root.addLayout(edit_row)

        hint = QLabel(
            "スクロールによる誤変更を防ぐため、スピン等は閲覧のみです。"
            "「設定を編集」から編集し、「変更を適用」で保存してください。"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        btn_row = QHBoxLayout()
        reload_btn = QPushButton("YAML再読込")
        reload_btn.clicked.connect(self._reload_yaml)
        btn_row.addWidget(reload_btn)

        self._control_cb_settings = QCheckBox("OS操作を有効化（危険）")
        self._control_cb_settings.setChecked(False)
        self._control_cb_settings.stateChanged.connect(lambda st: self._set_control_enabled_from_ui(bool(st)))
        btn_row.addWidget(self._control_cb_settings)

        exit_btn = QPushButton("アプリケーション終了")
        exit_btn.clicked.connect(self.exitRequested.emit)
        btn_row.addWidget(exit_btn)

        btn_row.addStretch(1)
        root.addLayout(btn_row)

        form_container = QWidget()
        form = QVBoxLayout(form_container)
        form.setContentsMargins(0, 0, 0, 0)

        form.addWidget(self._group_camera())
        form.addWidget(self._group_roi())
        form.addWidget(self._group_detection())
        form.addWidget(self._group_control())
        form.addWidget(self._group_anchoring())
        form.addWidget(self._group_scroll())
        form.addWidget(self._group_logging())
        form.addStretch(1)

        # 設定項目が多いので、フォーム領域だけ縦スクロール可能にする（上部ボタンは固定）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(form_container)
        root.addWidget(scroll, 1)

        self._reload_settings_widgets_from_store()
        self._set_settings_fields_enabled(False)
        return w

    def _reload_yaml(self) -> None:
        try:
            self._store.reload_from_disk()
        except Exception:
            pass
        if not self._settings_edit_mode:
            self._reload_settings_widgets_from_store()
        if not self._pie_edit_mode:
            self._reload_pie_widgets_from_store()

    def _group_camera(self) -> QGroupBox:
        g = QGroupBox("カメラ設定")
        layout = QFormLayout(g)
        s = self._store.get()

        device = QSpinBox()
        device.setRange(0, 16)
        device.setValue(int(s.camera.device_id))
        device.setToolTip("使用するカメラデバイスIDです。一般的に内蔵カメラは 0 です。")
        self._bind_setting("camera.device_id", device, camera_restart=True)
        layout.addRow("カメラID", device)

        width = QSpinBox()
        width.setRange(160, 1920)
        width.setValue(int(s.camera.width))
        width.setToolTip("プレビュー/解析に使う横解像度（幅）です。下げると軽くなります。")
        self._bind_setting("camera.width", width, camera_restart=True)
        layout.addRow("解像度（幅）", width)

        height = QSpinBox()
        height.setRange(120, 1080)
        height.setValue(int(s.camera.height))
        height.setToolTip("プレビュー/解析に使う縦解像度（高さ）です。下げると軽くなります。")
        self._bind_setting("camera.height", height, camera_restart=True)
        layout.addRow("解像度（高さ）", height)

        fps = QSpinBox()
        fps.setRange(5, 120)
        fps.setValue(int(s.camera.fps))
        fps.setToolTip("カメラの目標FPSです。高すぎると負荷が増えます。")
        self._bind_setting("camera.fps", fps, camera_restart=True)
        layout.addRow("FPS", fps)

        roi_enabled = QCheckBox("enabled")
        roi_enabled.setChecked(bool(s.camera.roi.enabled))
        roi_enabled.setToolTip("有効にすると解析範囲（ROI）を使って処理負荷を下げます。")
        self._bind_setting("camera.roi.enabled", roi_enabled)
        layout.addRow("ROI（解析範囲）", roi_enabled)

        return g

    def _group_roi(self) -> QGroupBox:
        g = QGroupBox("ROI（解析範囲）の詳細")
        layout = QFormLayout(g)
        s = self._store.get()

        rx = QDoubleSpinBox()
        rx.setRange(0.0, 1.0)
        rx.setSingleStep(0.01)
        rx.setDecimals(3)
        self._tune_double_spin(rx)
        rx.setValue(float(s.camera.roi.x))
        rx.setToolTip("ROIの左上X（正規化 0.0〜1.0）。")
        self._bind_setting("camera.roi.x", rx)
        layout.addRow("ROI X（左）", rx)

        ry = QDoubleSpinBox()
        ry.setRange(0.0, 1.0)
        ry.setSingleStep(0.01)
        ry.setDecimals(3)
        self._tune_double_spin(ry)
        ry.setValue(float(s.camera.roi.y))
        ry.setToolTip("ROIの左上Y（正規化 0.0〜1.0）。")
        self._bind_setting("camera.roi.y", ry)
        layout.addRow("ROI Y（上）", ry)

        rw = QDoubleSpinBox()
        rw.setRange(0.0, 1.0)
        rw.setSingleStep(0.01)
        rw.setDecimals(3)
        self._tune_double_spin(rw)
        rw.setValue(float(s.camera.roi.w))
        rw.setToolTip("ROIの幅（正規化 0.0〜1.0）。x+wが1.0を超えないようにしてください。")
        self._bind_setting("camera.roi.w", rw)
        layout.addRow("ROI 幅", rw)

        rh = QDoubleSpinBox()
        rh.setRange(0.0, 1.0)
        rh.setSingleStep(0.01)
        rh.setDecimals(3)
        self._tune_double_spin(rh)
        rh.setValue(float(s.camera.roi.h))
        rh.setToolTip("ROIの高さ（正規化 0.0〜1.0）。y+hが1.0を超えないようにしてください。")
        self._bind_setting("camera.roi.h", rh)
        layout.addRow("ROI 高さ", rh)

        return g

    def _group_detection(self) -> QGroupBox:
        g = QGroupBox("検出（ハンドトラッキング）")
        layout = QFormLayout(g)
        s = self._store.get()

        det = QDoubleSpinBox()
        det.setRange(0.0, 1.0)
        det.setSingleStep(0.05)
        det.setDecimals(3)
        self._tune_double_spin(det)
        det.setValue(float(s.detection.min_detection_confidence))
        det.setToolTip("手の検出のしきい値です。高いほど誤検出は減りますが検出しにくくなります。")
        self._bind_setting("detection.min_detection_confidence", det)
        layout.addRow("検出信頼度（初回）", det)

        tr = QDoubleSpinBox()
        tr.setRange(0.0, 1.0)
        tr.setSingleStep(0.05)
        tr.setDecimals(3)
        self._tune_double_spin(tr)
        tr.setValue(float(s.detection.min_tracking_confidence))
        tr.setToolTip("追跡（フレーム間）のしきい値です。高いほど安定しますがロストしやすくなります。")
        self._bind_setting("detection.min_tracking_confidence", tr)
        layout.addRow("追跡信頼度", tr)

        fs = QSpinBox()
        fs.setRange(0, 10)
        fs.setValue(int(s.detection.frame_skip))
        fs.setToolTip("解析を間引くフレーム数です。0で毎フレーム解析（重い）。増やすと軽くなります。")
        self._bind_setting("detection.frame_skip", fs)
        layout.addRow("フレーム間引き", fs)

        return g

    def _group_control(self) -> QGroupBox:
        g = QGroupBox("操作（移動/クリック）")
        layout = QFormLayout(g)
        s = self._store.get()

        ps = QComboBox()
        ps.addItem("人差し指先（ブレ抑制）", "index_tip")
        ps.addItem("人差し指+中指の平均（従来）", "index_middle_avg")
        ps.addItem("手首（手の付け根・クリック時のブレ抑制）", "wrist")
        current_ps = getattr(s.control, "pointer_source", "index_middle_avg")
        idx = ps.findData(str(current_ps))
        ps.setCurrentIndex(idx if idx >= 0 else 1)
        ps.setToolTip(
            "カーソル移動の算出元です（クリック判定・ジェスチャは変更しません）。\n"
            "親指を寄せるクリック動作で指先が大きく動く場合は「手首」が安定しやすいです。"
        )
        self._bind_setting("control.pointer_source", ps)
        layout.addRow("カーソル基準点", ps)

        # 基本感度（軸別未指定のときの共通値）。軸別を直接いじる運用が多いが、yaml互換のため残す。
        s_all = QDoubleSpinBox()
        s_all.setRange(0.1, 6.0)
        s_all.setSingleStep(0.1)
        s_all.setDecimals(3)
        self._tune_double_spin(s_all)
        s_all.setValue(float(s.control.sensitivity))
        s_all.setToolTip("基本のマウス感度です（互換用）。通常は左右/上下を個別に調整します。")
        self._bind_setting("control.sensitivity", s_all)
        layout.addRow("マウス感度（共通）", s_all)

        sx = QDoubleSpinBox()
        sx.setRange(0.1, 6.0)
        sx.setSingleStep(0.1)
        sx.setDecimals(3)
        self._tune_double_spin(sx)
        sx.setValue(float(s.control.sensitivity_x))
        sx.setToolTip(
            "左右方向のマウス移動感度です。\n"
            "- 大きい: 少ない手移動で大きく動く（速い/暴れやすい）\n"
            "- 小さい: 細かい操作がしやすい（端まで届きにくい）"
        )
        self._bind_setting("control.sensitivity_x", sx)
        layout.addRow("マウス感度（左右）", sx)

        sy = QDoubleSpinBox()
        sy.setRange(0.1, 6.0)
        sy.setSingleStep(0.1)
        sy.setDecimals(3)
        self._tune_double_spin(sy)
        sy.setValue(float(s.control.sensitivity_y))
        sy.setToolTip(
            "上下方向のマウス移動感度です。\n"
            "- 大きい: 少ない手移動で大きく動く（速い/暴れやすい）\n"
            "- 小さい: 細かい操作がしやすい（端まで届きにくい）"
        )
        self._bind_setting("control.sensitivity_y", sy)
        layout.addRow("マウス感度（上下）", sy)

        sf = QDoubleSpinBox()
        sf.setRange(0.01, 1.0)
        sf.setSingleStep(0.05)
        sf.setDecimals(3)
        self._tune_double_spin(sf)
        sf.setValue(float(s.control.smoothing_factor))
        sf.setToolTip(
            "平滑化（EMA）係数です。\n"
            "- 大きい: 追従が速い（震えが出やすい）\n"
            "- 小さい: 滑らか（遅延/もっさりしやすい）\n"
            "カクつく場合は 0.35→0.45 など少し上げると改善することがあります。"
        )
        self._bind_setting("control.smoothing_factor", sf)
        layout.addRow("スムージング係数", sf)

        ct = QDoubleSpinBox()
        ct.setRange(0.0, 0.2)
        ct.setSingleStep(0.005)
        ct.setDecimals(4)
        self._tune_double_spin(ct)
        ct.setValue(float(s.control.click_threshold))
        ct.setToolTip(
            "接触（pinch）判定の距離しきい値です。\n"
            "- 大きい: 浅いつまみでもON（誤判定も増えやすい）\n"
            "- 小さい: しっかりつままないとON（誤判定は減る）"
        )
        self._bind_setting("control.click_threshold", ct)
        layout.addRow("クリックしきい値", ct)

        pre = QDoubleSpinBox()
        pre.setRange(0.0, 0.3)
        pre.setSingleStep(0.005)
        pre.setDecimals(4)
        self._tune_double_spin(pre)
        pre.setValue(float(s.control.cursor_anchoring.pre_contact_threshold))
        pre.setToolTip(
            "アンカリング（クリック時の固定）開始の距離しきい値です。\n"
            "- 大きい: 早い段階で固定（クリックは安定/通常移動が止まりやすい）\n"
            "- 小さい: 固定が遅い（通常移動は軽い/クリック時にズレやすい）"
        )
        self._bind_setting("control.cursor_anchoring.pre_contact_threshold", pre)
        layout.addRow("固定開始（予兆）しきい値", pre)

        ti = QSpinBox()
        ti.setRange(50, 1200)
        ti.setValue(int(s.control.tap_interval_ms))
        ti.setToolTip("タップの連続判定の時間幅（ms）です。遅めのタップなら大きめにします。")
        self._bind_setting("control.tap_interval_ms", ti)
        layout.addRow("タップ間隔（ms）", ti)

        dh = QSpinBox()
        dh.setRange(50, 3000)
        dh.setValue(int(getattr(s.control, "drag_hold_ms", s.control.tap_interval_ms)))
        dh.setToolTip(
            "ドラッグ開始（長押し）の時間（ms）です。\n"
            "- 大きい: ドラッグ誤発火が減る（ドラッグ開始が遅い）\n"
            "- 小さい: すぐドラッグ開始（クリックしたいのにドラッグになりやすい）"
        )
        self._bind_setting("control.drag_hold_ms", dh)
        layout.addRow("ドラッグ開始（長押しms）", dh)

        dg = QSpinBox()
        dg.setRange(0, 2000)
        dg.setValue(int(getattr(s.control, "drag_contact_grace_ms", 120)))
        dg.setToolTip(
            "ドラッグ中に接触判定が一瞬OFFになっても、この時間未満なら押下を維持します。"
            "範囲選択中のmouseUp誤発火を減らします。"
        )
        self._bind_setting("control.drag_contact_grace_ms", dg)
        layout.addRow("ドラッグ中:接触OFF猶予（ms）", dg)

        df = QSpinBox()
        df.setRange(1, 30)
        df.setValue(int(getattr(s.control, "drag_contact_release_frames", 4)))
        df.setToolTip(
            "ドラッグ中に接触OFFがこのフレーム数連続したら離脱（mouseUp）します。"
            "猶予よりも「本当に離した」判定を優先したい場合に増やします。"
        )
        self._bind_setting("control.drag_contact_release_frames", df)
        layout.addRow("ドラッグ中:離脱確定フレーム", df)

        mf = QSpinBox()
        mf.setRange(1, 30)
        mf.setValue(int(s.control.mouse_mode_stable_frames))
        mf.setToolTip(
            "マウスモードを確定するまでの安定フレーム数です。\n"
            "- 大きい: 誤判定が減る（動き出しが遅く/カクつきやすい）\n"
            "- 小さい: 反応が速い（誤判定が増えやすい）"
        )
        self._bind_setting("control.mouse_mode_stable_frames", mf)
        layout.addRow("モード確定フレーム数", mf)

        dz = QDoubleSpinBox()
        dz.setRange(0.0, 0.05)
        dz.setSingleStep(0.001)
        dz.setDecimals(4)
        self._tune_double_spin(dz)
        dz.setValue(float(s.control.relative_move_deadzone))
        dz.setToolTip(
            "相対移動のデッドゾーン（小さな揺れを無視する幅）です。\n"
            "- 大きい: 静止時の揺れが減る（小さな移動が反映されにくい）\n"
            "- 小さい: 微小操作が効く（手ブレが反映されやすい）"
        )
        self._bind_setting("control.relative_move_deadzone", dz)
        layout.addRow("死域（ブレ無視）", dz)

        vg = QDoubleSpinBox()
        vg.setRange(0.5, 4.0)
        vg.setSingleStep(0.05)
        vg.setDecimals(2)
        self._tune_double_spin(vg)
        vg.setValue(float(getattr(s.control, "relative_move_vertical_gain", 1.35)))
        vg.setToolTip(
            "相対移動の縦方向だけの倍率です（横は 1 固定）。\n"
            "カメラ構図や ROI で「縦に動かしてもΔが小さい」ときに上げます（例: 1.35〜2.0）。"
        )
        self._bind_setting("control.relative_move_vertical_gain", vg)
        layout.addRow("縦移動ブースト倍率", vg)

        cl = QDoubleSpinBox()
        # settings.yaml の既定が 0.25 のため、上限 0.2 だと範囲外になり編集が破綻しやすい
        cl.setRange(0.001, 0.5)
        cl.setSingleStep(0.005)
        cl.setDecimals(4)
        self._tune_double_spin(cl)
        cl.setValue(float(s.control.relative_move_clamp_th))
        cl.setToolTip(
            "1フレームあたりの最大移動Δ（ジャンプ抑制）です。\n"
            "- 大きい: 速く動く/引っかかりが減る（検出が飛ぶと大ジャンプしやすい）\n"
            "- 小さい: ジャンプに強い（動きが段付き/カクつきやすい）\n"
            "カクカクする場合は 0.15→0.25 のように上げると改善することがあります。"
        )
        self._bind_setting("control.relative_move_clamp_th", cl)
        layout.addRow("ジャンプ抑制しきい値", cl)

        cb = QCheckBox()
        cb.setChecked(bool(s.control.click_requires_middle_bent))
        cb.setToolTip(
            "ONにすると「中指を曲げた状態」をクリック条件に追加します。\n"
            "- ON: 誤クリックが減る（クリック姿勢が必要）\n"
            "- OFF: クリックが通りやすい（誤クリックが増えやすい）"
        )
        self._bind_setting("control.click_requires_middle_bent", cb)
        layout.addRow("クリック時に中指を曲げる", cb)

        ms = QCheckBox()
        ms.setChecked(bool(s.control.move_suppress_on_middle_bent))
        ms.setToolTip(
            "中指が曲がり始めたらカーソル移動を抑制します。\n"
            "- ON: クリック時のズレが減る（操作が重く感じることがある）\n"
            "- OFF: 追従が軽い（クリック時にズレやすい）"
        )
        self._bind_setting("control.move_suppress_on_middle_bent", ms)
        layout.addRow("中指曲げで移動抑制", ms)

        return g

    def _group_anchoring(self) -> QGroupBox:
        g = QGroupBox("カーソル固定（アンカリング）")
        layout = QFormLayout(g)
        s = self._store.get()

        en = QCheckBox()
        en.setChecked(bool(s.control.cursor_anchoring.enabled))
        en.setToolTip(
            "クリックの予兆〜接触中にカーソルを固定し、クリック精度を優先します。\n"
            "- ON: 小さいUIが押しやすい（固定感が出る）\n"
            "- OFF: 操作が軽い（クリック時に逃げやすい）"
        )
        self._bind_setting("control.cursor_anchoring.enabled", en)
        layout.addRow("有効化", en)

        pre = QDoubleSpinBox()
        pre.setRange(0.0, 0.3)
        pre.setSingleStep(0.005)
        pre.setDecimals(4)
        self._tune_double_spin(pre)
        pre.setValue(float(s.control.cursor_anchoring.pre_contact_threshold))
        pre.setToolTip("接触の手前（予兆）で固定し始める距離です。")
        self._bind_setting("control.cursor_anchoring.pre_contact_threshold", pre)
        layout.addRow("固定開始（予兆）しきい値", pre)

        ff = QSpinBox()
        ff.setRange(0, 30)
        ff.setValue(int(s.control.cursor_anchoring.freeze_frames))
        ff.setToolTip(
            "固定するフレーム数です。\n"
            "- 大きい: ズレが減る（固定が長く重い）\n"
            "- 小さい: 軽い（クリック時のズレが出やすい）"
        )
        self._bind_setting("control.cursor_anchoring.freeze_frames", ff)
        layout.addRow("固定フレーム数", ff)

        ov = QDoubleSpinBox()
        ov.setRange(0.0, 1.0)
        ov.setSingleStep(0.01)
        ov.setDecimals(4)
        self._tune_double_spin(ov)
        ov.setValue(float(s.control.cursor_anchoring.override_smoothing_factor_ema))
        ov.setToolTip("固定中のEMA係数です。0に近いほどほぼ固定、1に近いほど追従します。")
        self._bind_setting("control.cursor_anchoring.override_smoothing_factor_ema", ov)
        layout.addRow("固定中EMA係数", ov)

        return g

    def _group_scroll(self) -> QGroupBox:
        g = QGroupBox("操作（スクロール）")
        layout = QFormLayout(g)
        s = self._store.get()

        ss = QSpinBox()
        ss.setRange(1, 5000)
        ss.setValue(int(s.control.scroll_sensitivity))
        ss.setToolTip(
            "スクロール速度（移動量の倍率）です。\n"
            "- 大きい: 速い（少し動かすだけで大きくスクロール）\n"
            "- 小さい: 細かい（長い距離のスクロールは遅い）"
        )
        self._bind_setting("control.scroll_sensitivity", ss)
        layout.addRow("スクロール感度", ss)

        sd = QDoubleSpinBox()
        sd.setRange(0.0, 0.05)
        sd.setSingleStep(0.001)
        sd.setDecimals(4)
        self._tune_double_spin(sd)
        sd.setValue(float(s.control.scroll_deadzone))
        sd.setToolTip(
            "スクロール開始のデッドゾーンです。\n"
            "- 大きい: 勝手スクロールが減る（意図したスクロール開始が難しい）\n"
            "- 小さい: すぐ反応（勝手スクロールが起きやすい）"
        )
        self._bind_setting("control.scroll_deadzone", sd)
        layout.addRow("スクロール死域", sd)

        return g

    def _group_logging(self) -> QGroupBox:
        g = QGroupBox("ログ")
        layout = QFormLayout(g)
        s = self._store.get()

        # log_dir, log_file_name は文字列だが、現状UIは数値/checkbox中心なので最小実装として表示のみ＋ツールチップに留める
        # （研究用途での誤入力防止。必要なら次回QLineEditで編集可能にする）
        dir_label = QLabel(str(s.logging.log_dir))
        dir_label.setToolTip("ログ保存先ディレクトリです（現在はGUIから編集不可）。")
        self._log_dir_label = dir_label
        layout.addRow("保存先", dir_label)

        file_label = QLabel(str(s.logging.log_file_name))
        file_label.setToolTip("ログファイル名です（現在はGUIから編集不可）。")
        self._log_file_label = file_label
        layout.addRow("ファイル名", file_label)

        mb = QSpinBox()
        mb.setRange(1024, 1024 * 1024 * 1024)
        mb.setSingleStep(1024 * 1024)
        mb.setValue(int(s.logging.max_bytes))
        mb.setToolTip("このサイズ（bytes）を超えたらローテーションします。")
        self._bind_setting("logging.max_bytes", mb)
        layout.addRow("最大サイズ（bytes）", mb)

        bc = QSpinBox()
        bc.setRange(0, 100)
        bc.setValue(int(s.logging.backup_count))
        bc.setToolTip("保持するバックアップ数です。")
        self._bind_setting("logging.backup_count", bc)
        layout.addRow("バックアップ数", bc)

        fl = QCheckBox()
        fl.setChecked(bool(s.logging.flush))
        fl.setToolTip("ONにすると各イベントを即時flushします（研究ログの欠落を防止）。")
        self._bind_setting("logging.flush", fl)
        layout.addRow("即時flush", fl)

        return g

    def _set_value(self, dotted: str, value: Any) -> None:
        try:
            self._store.set_value(dotted, value)
        except Exception:
            pass

    # -----------------------------
    # PieMenu settings tab
    # -----------------------------
    def _build_pie_menu_tab(self) -> QWidget:
        self._pie_bindings.clear()
        self._pie_preset2_combos.clear()
        self._pie_extra_buttons.clear()

        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        pie_edit_row = QHBoxLayout()
        self._btn_pie_edit = QPushButton("Pie設定を編集")
        self._btn_pie_apply = QPushButton("変更を適用")
        self._btn_pie_cancel = QPushButton("キャンセル")
        self._btn_pie_apply.setVisible(False)
        self._btn_pie_cancel.setVisible(False)
        self._btn_pie_edit.clicked.connect(self._on_pie_edit_clicked)
        self._btn_pie_apply.clicked.connect(self._on_pie_apply_clicked)
        self._btn_pie_cancel.clicked.connect(self._on_pie_cancel_clicked)
        pie_edit_row.addWidget(self._btn_pie_edit)
        pie_edit_row.addWidget(self._btn_pie_apply)
        pie_edit_row.addWidget(self._btn_pie_cancel)
        pie_edit_row.addStretch(1)
        root.addLayout(pie_edit_row)

        info = QLabel(
            "表示順は Preset 1 → 2 → 3 です。\n"
            "Preset 1/3 はユーザー定義、Preset 2 は Media 用の固定アクション配置です。\n"
            "項目の実行確定は『ダブルピンチ』（つまみを離して再度つまむ）です。\n"
            "Type=Shortcut は PyAutoGUI 形式（例: ctrl+shift+p / command+space）を想定します。"
        )
        info.setWordWrap(True)
        root.addWidget(info)

        s = self._store.get()

        # PieMenuの決定（pinch）しきい値
        th_row = QWidget()
        th_lay = QHBoxLayout(th_row)
        th_lay.setContentsMargins(0, 0, 0, 0)
        th_label = QLabel("決定しきい値（大きいほど判定が通りやすい）")
        th_lay.addWidget(th_label)
        th = QDoubleSpinBox()
        th.setRange(0.02, 0.2)
        th.setSingleStep(0.005)
        th.setDecimals(4)
        self._tune_double_spin(th)
        th.setValue(float(getattr(s.pie_menu, "click_threshold", 0.085)))
        th.setToolTip(
            "PieMenu表示中の『つまみ』判定に使う距離です。\n"
            "確定自体はダブルピンチ（下記の間隔内に2回つまみ開始）です。"
        )
        self._bind_pie("pie_menu.click_threshold", th)
        th_lay.addWidget(th)

        gap_row = QWidget()
        gap_lay = QHBoxLayout(gap_row)
        gap_lay.setContentsMargins(0, 0, 0, 0)
        gap_lay.addWidget(QLabel("確定: 2回つまみの最大間隔（ms・ダブルクリック相当）"))
        gap = QSpinBox()
        gap.setRange(200, 4000)
        gap.setSingleStep(50)
        gap.setValue(int(getattr(s.pie_menu, "confirm_double_pinch_max_gap_ms", 900)))
        gap.setToolTip(
            "1回目のつまみを離し、2回目のつまみ開始までの許容時間です。\n"
            "短いほど誤確定しにくく、長いほどゆっくりでも確定できます。"
        )
        self._bind_pie("pie_menu.confirm_double_pinch_max_gap_ms", gap)
        gap_lay.addWidget(gap)

        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        lay.addWidget(th_row)
        lay.addWidget(gap_row)

        # Preset 1 → 2 → 3（いずれも同じスクロール内）
        lay.addWidget(self._group_pie_preset("Preset 1 (Custom)", preset_key="custom_1"))
        lay.addWidget(self._group_pie_preset2_layout())
        lay.addWidget(self._group_pie_preset("Preset 3 (Custom)", preset_key="custom_3"))
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        self._reload_pie_widgets_from_store()
        self._set_pie_fields_enabled(False)
        return w

    def _group_pie_preset2_layout(self) -> QGroupBox:
        g = QGroupBox("Preset 2 (Media) 配置")
        form = QFormLayout(g)
        s = self._store.get()
        layout_ids = list(getattr(s.pie_menu, "preset2_layout"))

        # 選択肢
        items = [(a.label, a.id) for a in MEDIA_ACTIONS]

        for i in range(1, 9):
            cb = QComboBox()
            for label, aid in items:
                cb.addItem(label, aid)
            cur = str(layout_ids[i - 1]) if i - 1 < len(layout_ids) else items[i - 1][1]
            idx = cb.findData(cur)
            cb.setCurrentIndex(idx if idx >= 0 else 0)

            self._pie_preset2_combos.append(cb)
            form.addRow(f"Slot {i}", cb)

        return g

    def _group_pie_preset(self, title: str, *, preset_key: str) -> QGroupBox:
        g = QGroupBox(title)
        form = QFormLayout(g)

        s = self._store.get()
        preset = s.pie_menu.custom_1 if preset_key == "custom_1" else s.pie_menu.custom_3

        for i in range(1, 9):
            slot = preset.slots[i - 1]

            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)

            label_edit = QLineEdit(str(slot.label))
            label_edit.setPlaceholderText("Label")
            label_path = f"pie_menu.presets.{preset_key}.slots.{i}.label"
            self._bind_pie(label_path, label_edit)
            row_lay.addWidget(label_edit, 2)

            typ = QComboBox()
            typ.addItem("Shortcut (Key)", "shortcut")
            typ.addItem("Application (Path)", "application")
            cur_idx = typ.findData(str(slot.type))
            typ.setCurrentIndex(cur_idx if cur_idx >= 0 else 0)
            type_path = f"pie_menu.presets.{preset_key}.slots.{i}.type"
            self._bind_pie(type_path, typ)
            row_lay.addWidget(typ, 1)

            value_edit = ShortcutValueLineEdit(typ)
            value_edit.setText(str(slot.value))
            value_edit.setPlaceholderText("Value（Shortcutの場合はキー入力で設定）")
            value_path = f"pie_menu.presets.{preset_key}.slots.{i}.value"
            self._bind_pie(value_path, value_edit)
            row_lay.addWidget(value_edit, 3)

            browse = QPushButton("参照…")

            def _browse_into_value(*, ve: QLineEdit) -> None:
                try:
                    path, _ = QFileDialog.getOpenFileName(self, "アプリ/ファイルを選択")
                    if not path:
                        return
                    ve.setText(path)
                except Exception:
                    pass

            browse.clicked.connect(lambda _, ve=value_edit: _browse_into_value(ve=ve))
            self._pie_extra_buttons.append(browse)
            row_lay.addWidget(browse)

            form.addRow(f"Slot {i}", row)

        return g

    # -----------------------------
    # Motion tab
    # -----------------------------
    def _build_motion_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        ctl_row = QHBoxLayout()
        self._preview_cb = QCheckBox("プレビュー表示（重い）")
        self._preview_cb.setChecked(True)
        self._preview_cb.stateChanged.connect(lambda st: self.previewEnabledChanged.emit(bool(st)))
        ctl_row.addWidget(self._preview_cb)

        self._control_cb = QCheckBox("OS操作を有効化（危険）")
        self._control_cb.setChecked(False)
        self._control_cb.stateChanged.connect(lambda st: self._set_control_enabled_from_ui(bool(st)))
        ctl_row.addWidget(self._control_cb)
        ctl_row.addStretch(1)
        root.addLayout(ctl_row)

        self._status_label = QLabel("status: -")
        root.addWidget(self._status_label)

        self._frame_label = _AspectFitPixmapLabel()
        self._frame_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # 横長映像を縦長プレビュー領域に「全体フィット」すると上下のレターボックスが大きくなりやすい。
        # まずは横幅基準でスケールし、縦が溢れる場合はスクロールで確認できるようにする。
        self._motion_scroll = QScrollArea()
        # False: 子ウィジェットの高さをコンテンツに合わせ、縦スクロールを成立させる
        self._motion_scroll.setWidgetResizable(False)
        self._motion_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._motion_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._motion_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._motion_scroll.setWidget(self._frame_label)
        self._motion_scroll.setMinimumHeight(240)
        root.addWidget(self._motion_scroll, 1)

        return w

    def set_control_enabled(self, enabled: bool, *, emit: bool = True) -> None:
        """OS操作の有効/無効をUI間で同期しつつ切り替える。"""

        enabled = bool(enabled)

        # UI同期（相互にstateChangedが飛ぶのでblockSignalsで抑制）
        try:
            self._control_cb.blockSignals(True)
            self._control_cb.setChecked(enabled)
        finally:
            self._control_cb.blockSignals(False)

        try:
            self._control_cb_settings.blockSignals(True)
            self._control_cb_settings.setChecked(enabled)
        finally:
            self._control_cb_settings.blockSignals(False)

        if emit:
            self.controlEnabledChanged.emit(enabled)

    def _set_control_enabled_from_ui(self, enabled: bool) -> None:
        self.set_control_enabled(enabled, emit=True)

    def _on_frame_ready(self, qimg: QImage) -> None:
        self._motion_last_qimage = qimg
        self._layout_motion_preview()

    def _layout_motion_preview(self) -> None:
        if not hasattr(self, "_motion_scroll"):
            return
        if self._motion_last_qimage is None:
            return

        vp_w = max(1, int(self._motion_scroll.viewport().width()))
        src = QPixmap.fromImage(self._motion_last_qimage)
        if src.isNull():
            return

        scaled = src.scaledToWidth(vp_w, Qt.TransformationMode.SmoothTransformation)
        self._frame_label.setFixedSize(scaled.size())
        self._frame_label.set_scaled_pixmap(scaled)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._layout_motion_preview()

    def _on_status_ready(self, status: dict) -> None:
        try:
            dbg = status.get("controller") or {}
            ctrl = status.get("control") or {}
            dist = status.get("contact_distance")
            dist_s = f"{float(dist):.4f}" if isinstance(dist, (int, float)) else str(dist)

            self._status_label.setText(
                "vision: "
                f"mode={status.get('mode')} fc={status.get('finger_count')} "
                f"idx={int(bool(status.get('index_extended')))} mid={int(bool(status.get('middle_extended')))} "
                f"c={int(bool(status.get('contact')))} pre={int(bool(status.get('pre_contact')))} dist={dist_s} "
                f"lat={float(status.get('latency_ms')):.1f}ms fps={float(status.get('fps')):.1f}\n"
                "control: "
                f"os={int(bool(status.get('control_enabled')))} apply={int(bool(dbg.get('apply_actions')))} "
                f"streak={dbg.get('mouse_mode_streak')} "
                f"pose={int(bool(dbg.get('click_pose_active')))} supMid={int(bool(dbg.get('suppress_move_middle')))} "
                f"drag={int(bool(dbg.get('dragging')))} hold={dbg.get('contact_hold_ms')} tapq={dbg.get('tap_queue_len')} "
                f"freeze={int(bool(dbg.get('anchoring_freeze')))} rawA={int(bool(dbg.get('anchoring_active_raw')))} "
                f"moved={int(bool(ctrl.get('moved')))} L={int(bool(ctrl.get('left_clicked')))} "
                f"R={int(bool(ctrl.get('right_clicked')))} dD={int(bool(ctrl.get('drag_down')))} dU={int(bool(ctrl.get('drag_up')))}"
            )
        except Exception:
            self._status_label.setText(str(status))

    def _on_worker_error(self, msg: str) -> None:
        self._status_label.setText(f"error: {msg}")

    def _on_tab_changed(self, idx: int) -> None:
        old = int(getattr(self, "_prev_tab_index", 0))
        if old == 0 and idx != 0 and self._settings_edit_mode:
            self._on_settings_cancel_clicked()
        if old == 1 and idx != 1 and self._pie_edit_mode:
            self._on_pie_cancel_clicked()
        self._prev_tab_index = int(idx)

        # モーションタブが見えている時だけプレビューをONにする
        is_motion = self._tabs.tabText(idx) == "モーションテスト"
        self.previewEnabledChanged.emit(bool(is_motion and self._preview_cb.isChecked()))
        # マニュアルタブ表示時に docs/ を再読み込み（編集反映・起動中の更新向け）
        if self._tabs.tabText(idx) == "マニュアル":
            self._reload_manual()

    # -----------------------------
    # Log tab
    # -----------------------------
    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        root.addWidget(self._log_view, 1)

        self._log_timer = QTimer(self)
        self._log_timer.setInterval(500)
        self._log_timer.timeout.connect(self._refresh_logs)
        self._log_timer.start()

        return w

    def _refresh_logs(self) -> None:
        try:
            s = self._store.get()
            path = Path(s.logging.log_dir) / s.logging.log_file_name
            if not path.exists():
                return
            # 最新が先頭なので先頭から読む
            lines = path.read_text(encoding="utf-8").splitlines()
            head = lines[:200]
            # 見やすいよう整形
            out = []
            for ln in head:
                try:
                    obj = json.loads(ln)
                    out.append(json.dumps(obj, ensure_ascii=False))
                except Exception:
                    out.append(ln)
            self._log_view.setPlainText("\n".join(out))
        except Exception:
            pass

    # -----------------------------
    # Manual tab
    # -----------------------------
    def _build_manual_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        btn_row = QHBoxLayout()
        reload_btn = QPushButton("マニュアル再読込")
        reload_btn.setToolTip("docs/ 内の Markdown をディスクから読み直して表示を更新します。")
        reload_btn.clicked.connect(self._reload_manual)
        btn_row.addWidget(reload_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self._manual_view = QTextBrowser()
        self._manual_view.setOpenExternalLinks(True)
        # Markdown表示（QtのMarkdownサブセット）。分割ドキュメントを連結して表示する。
        self._manual_view.setMarkdown(self._load_manual_text())
        root.addWidget(self._manual_view, 1)
        return w

    def _reload_manual(self) -> None:
        if not hasattr(self, "_manual_view"):
            return
        self._manual_view.setMarkdown(self._load_manual_text())
        self._manual_view.verticalScrollBar().setValue(0)

    def _load_manual_text(self) -> str:
        docs_dir = Path("docs")
        if not docs_dir.exists():
            return "docs/ が見つかりません。操作ガイドは docs/ に追加してください。"
        # docs/ 直下の *.md をファイル名順に連結（分割ドキュメントの読み込み順をファイル名で制御）
        md_files = sorted(docs_dir.glob("*.md"))
        if not md_files:
            return "docs/ にMarkdownがありません。操作ガイドを追加してください。"
        parts = []
        for p in md_files:
            try:
                body = p.read_text(encoding="utf-8").strip()
                if not body:
                    continue
                # 見出しで区切る（ASCIIの大きな区切り線は使わない）
                parts.append(f"## {p.name}\n\n{body}")
            except Exception:
                pass
        return "\n\n---\n\n".join(parts) if parts else "マニュアルを読み込めませんでした。"

