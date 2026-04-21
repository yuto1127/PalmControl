from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
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

        self.setWindowTitle("PalmControl")
        self.resize(980, 720)

        self._tabs = QTabWidget()
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

    # -----------------------------
    # Settings tab
    # -----------------------------
    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(0, 0, 0, 0)

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
        return w

    def _reload_yaml(self) -> None:
        try:
            self._store.reload_from_disk()
        except Exception:
            pass

    def _group_camera(self) -> QGroupBox:
        g = QGroupBox("カメラ設定")
        layout = QFormLayout(g)
        s = self._store.get()

        device = QSpinBox()
        device.setRange(0, 16)
        device.setValue(int(s.camera.device_id))
        device.setToolTip("使用するカメラデバイスIDです。一般的に内蔵カメラは 0 です。")
        device.valueChanged.connect(lambda v: self._set_and_restart("camera.device_id", int(v)))
        layout.addRow("カメラID", device)

        width = QSpinBox()
        width.setRange(160, 1920)
        width.setValue(int(s.camera.width))
        width.setToolTip("プレビュー/解析に使う横解像度（幅）です。下げると軽くなります。")
        width.valueChanged.connect(lambda v: self._set_and_restart("camera.width", int(v)))
        layout.addRow("解像度（幅）", width)

        height = QSpinBox()
        height.setRange(120, 1080)
        height.setValue(int(s.camera.height))
        height.setToolTip("プレビュー/解析に使う縦解像度（高さ）です。下げると軽くなります。")
        height.valueChanged.connect(lambda v: self._set_and_restart("camera.height", int(v)))
        layout.addRow("解像度（高さ）", height)

        fps = QSpinBox()
        fps.setRange(5, 120)
        fps.setValue(int(s.camera.fps))
        fps.setToolTip("カメラの目標FPSです。高すぎると負荷が増えます。")
        fps.valueChanged.connect(lambda v: self._set_and_restart("camera.fps", int(v)))
        layout.addRow("FPS", fps)

        roi_enabled = QCheckBox("enabled")
        roi_enabled.setChecked(bool(s.camera.roi.enabled))
        roi_enabled.setToolTip("有効にすると解析範囲（ROI）を使って処理負荷を下げます。")
        roi_enabled.stateChanged.connect(lambda st: self._set_value("camera.roi.enabled", bool(st)))
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
        rx.setValue(float(s.camera.roi.x))
        rx.setToolTip("ROIの左上X（正規化 0.0〜1.0）。")
        rx.valueChanged.connect(lambda v: self._set_value("camera.roi.x", float(v)))
        layout.addRow("ROI X（左）", rx)

        ry = QDoubleSpinBox()
        ry.setRange(0.0, 1.0)
        ry.setSingleStep(0.01)
        ry.setDecimals(3)
        ry.setValue(float(s.camera.roi.y))
        ry.setToolTip("ROIの左上Y（正規化 0.0〜1.0）。")
        ry.valueChanged.connect(lambda v: self._set_value("camera.roi.y", float(v)))
        layout.addRow("ROI Y（上）", ry)

        rw = QDoubleSpinBox()
        rw.setRange(0.0, 1.0)
        rw.setSingleStep(0.01)
        rw.setDecimals(3)
        rw.setValue(float(s.camera.roi.w))
        rw.setToolTip("ROIの幅（正規化 0.0〜1.0）。x+wが1.0を超えないようにしてください。")
        rw.valueChanged.connect(lambda v: self._set_value("camera.roi.w", float(v)))
        layout.addRow("ROI 幅", rw)

        rh = QDoubleSpinBox()
        rh.setRange(0.0, 1.0)
        rh.setSingleStep(0.01)
        rh.setDecimals(3)
        rh.setValue(float(s.camera.roi.h))
        rh.setToolTip("ROIの高さ（正規化 0.0〜1.0）。y+hが1.0を超えないようにしてください。")
        rh.valueChanged.connect(lambda v: self._set_value("camera.roi.h", float(v)))
        layout.addRow("ROI 高さ", rh)

        return g

    def _group_detection(self) -> QGroupBox:
        g = QGroupBox("検出（ハンドトラッキング）")
        layout = QFormLayout(g)
        s = self._store.get()

        det = QDoubleSpinBox()
        det.setRange(0.0, 1.0)
        det.setSingleStep(0.05)
        det.setValue(float(s.detection.min_detection_confidence))
        det.setToolTip("手の検出のしきい値です。高いほど誤検出は減りますが検出しにくくなります。")
        det.valueChanged.connect(lambda v: self._set_value("detection.min_detection_confidence", float(v)))
        layout.addRow("検出信頼度（初回）", det)

        tr = QDoubleSpinBox()
        tr.setRange(0.0, 1.0)
        tr.setSingleStep(0.05)
        tr.setValue(float(s.detection.min_tracking_confidence))
        tr.setToolTip("追跡（フレーム間）のしきい値です。高いほど安定しますがロストしやすくなります。")
        tr.valueChanged.connect(lambda v: self._set_value("detection.min_tracking_confidence", float(v)))
        layout.addRow("追跡信頼度", tr)

        fs = QSpinBox()
        fs.setRange(0, 10)
        fs.setValue(int(s.detection.frame_skip))
        fs.setToolTip("解析を間引くフレーム数です。0で毎フレーム解析（重い）。増やすと軽くなります。")
        fs.valueChanged.connect(lambda v: self._set_value("detection.frame_skip", int(v)))
        layout.addRow("フレーム間引き", fs)

        return g

    def _group_control(self) -> QGroupBox:
        g = QGroupBox("操作（移動/クリック）")
        layout = QFormLayout(g)
        s = self._store.get()

        ps = QComboBox()
        ps.addItem("人差し指先（ブレ抑制）", "index_tip")
        ps.addItem("人差し指+中指の平均（従来）", "index_middle_avg")
        current_ps = getattr(s.control, "pointer_source", "index_middle_avg")
        idx = ps.findData(str(current_ps))
        ps.setCurrentIndex(idx if idx >= 0 else 1)
        ps.setToolTip("カーソル位置の算出元です。クリック姿勢（中指の曲げ伸ばし）でブレる場合は「人差し指先」を推奨します。")
        ps.currentIndexChanged.connect(lambda _: self._set_value("control.pointer_source", str(ps.currentData())))
        layout.addRow("カーソル基準点", ps)

        # 基本感度（軸別未指定のときの共通値）。軸別を直接いじる運用が多いが、yaml互換のため残す。
        s_all = QDoubleSpinBox()
        s_all.setRange(0.1, 6.0)
        s_all.setSingleStep(0.1)
        s_all.setValue(float(s.control.sensitivity))
        s_all.setToolTip("基本のマウス感度です（互換用）。通常は左右/上下を個別に調整します。")
        s_all.valueChanged.connect(lambda v: self._set_value("control.sensitivity", float(v)))
        layout.addRow("マウス感度（共通）", s_all)

        sx = QDoubleSpinBox()
        sx.setRange(0.1, 6.0)
        sx.setSingleStep(0.1)
        sx.setValue(float(s.control.sensitivity_x))
        sx.setToolTip("左右方向のマウス移動感度です。大きいほど少ない手移動でカーソルが動きます。")
        sx.valueChanged.connect(lambda v: self._set_value("control.sensitivity_x", float(v)))
        layout.addRow("マウス感度（左右）", sx)

        sy = QDoubleSpinBox()
        sy.setRange(0.1, 6.0)
        sy.setSingleStep(0.1)
        sy.setValue(float(s.control.sensitivity_y))
        sy.setToolTip("上下方向のマウス移動感度です。大きいほど少ない手移動でカーソルが動きます。")
        sy.valueChanged.connect(lambda v: self._set_value("control.sensitivity_y", float(v)))
        layout.addRow("マウス感度（上下）", sy)

        sf = QDoubleSpinBox()
        sf.setRange(0.01, 1.0)
        sf.setSingleStep(0.05)
        sf.setValue(float(s.control.smoothing_factor))
        sf.setToolTip("ポインタ座標の平滑化（EMA）係数です。大きいほど追従性が高く、小さいほど滑らかになります。")
        sf.valueChanged.connect(lambda v: self._set_value("control.smoothing_factor", float(v)))
        layout.addRow("スムージング係数", sf)

        ct = QDoubleSpinBox()
        ct.setRange(0.0, 0.2)
        ct.setSingleStep(0.005)
        ct.setValue(float(s.control.click_threshold))
        ct.setToolTip("親指と指先の距離がこの値以下で「接触（クリック候補）」と判定します。")
        ct.valueChanged.connect(lambda v: self._set_value("control.click_threshold", float(v)))
        layout.addRow("クリックしきい値", ct)

        pre = QDoubleSpinBox()
        pre.setRange(0.0, 0.3)
        pre.setSingleStep(0.005)
        pre.setValue(float(s.control.cursor_anchoring.pre_contact_threshold))
        pre.setToolTip("接触の手前（予兆）でカーソルを固定し始める距離です。大きいほど早く固定します。")
        pre.valueChanged.connect(lambda v: self._set_value("control.cursor_anchoring.pre_contact_threshold", float(v)))
        layout.addRow("固定開始（予兆）しきい値", pre)

        ti = QSpinBox()
        ti.setRange(50, 1200)
        ti.setValue(int(s.control.tap_interval_ms))
        ti.setToolTip("タップの連続判定の時間幅（ms）です。遅めのタップなら大きめにします。")
        ti.valueChanged.connect(lambda v: self._set_value("control.tap_interval_ms", int(v)))
        layout.addRow("タップ間隔（ms）", ti)

        dh = QSpinBox()
        dh.setRange(50, 3000)
        dh.setValue(int(getattr(s.control, "drag_hold_ms", s.control.tap_interval_ms)))
        dh.setToolTip(
            "接触（pinch）をこの時間以上 유지するとドラッグ開始（mouseDown）になります。"
            "確定後はドラッグ中のカーソル移動が有効になり、範囲選択ができます。"
        )
        dh.valueChanged.connect(lambda v: self._set_value("control.drag_hold_ms", int(v)))
        layout.addRow("ドラッグ開始（長押しms）", dh)

        dg = QSpinBox()
        dg.setRange(0, 2000)
        dg.setValue(int(getattr(s.control, "drag_contact_grace_ms", 120)))
        dg.setToolTip(
            "ドラッグ中に接触判定が一瞬OFFになっても、この時間未満なら押下を維持します。"
            "範囲選択中のmouseUp誤発火を減らします。"
        )
        dg.valueChanged.connect(lambda v: self._set_value("control.drag_contact_grace_ms", int(v)))
        layout.addRow("ドラッグ中:接触OFF猶予（ms）", dg)

        df = QSpinBox()
        df.setRange(1, 30)
        df.setValue(int(getattr(s.control, "drag_contact_release_frames", 4)))
        df.setToolTip(
            "ドラッグ中に接触OFFがこのフレーム数連続したら離脱（mouseUp）します。"
            "猶予よりも「本当に離した」判定を優先したい場合に増やします。"
        )
        df.valueChanged.connect(lambda v: self._set_value("control.drag_contact_release_frames", int(v)))
        layout.addRow("ドラッグ中:離脱確定フレーム", df)

        mf = QSpinBox()
        mf.setRange(1, 30)
        mf.setValue(int(s.control.mouse_mode_stable_frames))
        mf.setToolTip("マウスモードを確定するまでの安定フレーム数です。増やすと誤判定が減ります。")
        mf.valueChanged.connect(lambda v: self._set_value("control.mouse_mode_stable_frames", int(v)))
        layout.addRow("モード確定フレーム数", mf)

        dz = QDoubleSpinBox()
        dz.setRange(0.0, 0.05)
        dz.setSingleStep(0.001)
        dz.setValue(float(s.control.relative_move_deadzone))
        dz.setToolTip("小さな手ブレを無視する死域です。大きいほど静止時の揺れが減ります。")
        dz.valueChanged.connect(lambda v: self._set_value("control.relative_move_deadzone", float(v)))
        layout.addRow("死域（ブレ無視）", dz)

        cl = QDoubleSpinBox()
        cl.setRange(0.001, 0.2)
        cl.setSingleStep(0.005)
        cl.setValue(float(s.control.relative_move_clamp_th))
        cl.setToolTip("異常なジャンプを抑制する上限値です。検出が飛ぶ場合は小さめにします。")
        cl.valueChanged.connect(lambda v: self._set_value("control.relative_move_clamp_th", float(v)))
        layout.addRow("ジャンプ抑制しきい値", cl)

        cb = QCheckBox()
        cb.setChecked(bool(s.control.click_requires_middle_bent))
        cb.setToolTip("ONにすると「中指を曲げた状態」をクリック条件に追加し、誤操作を減らします。")
        cb.stateChanged.connect(lambda st: self._set_value("control.click_requires_middle_bent", bool(st)))
        layout.addRow("クリック時に中指を曲げる", cb)

        ms = QCheckBox()
        ms.setChecked(bool(s.control.move_suppress_on_middle_bent))
        ms.setToolTip("中指が曲がり始めたらカーソル移動を抑制し、クリック時のズレを減らします。")
        ms.stateChanged.connect(lambda st: self._set_value("control.move_suppress_on_middle_bent", bool(st)))
        layout.addRow("中指曲げで移動抑制", ms)

        return g

    def _group_anchoring(self) -> QGroupBox:
        g = QGroupBox("カーソル固定（アンカリング）")
        layout = QFormLayout(g)
        s = self._store.get()

        en = QCheckBox()
        en.setChecked(bool(s.control.cursor_anchoring.enabled))
        en.setToolTip("クリックの予兆〜接触中にカーソルを固定して、クリック精度を優先します。")
        en.stateChanged.connect(lambda st: self._set_value("control.cursor_anchoring.enabled", bool(st)))
        layout.addRow("有効化", en)

        pre = QDoubleSpinBox()
        pre.setRange(0.0, 0.3)
        pre.setSingleStep(0.005)
        pre.setValue(float(s.control.cursor_anchoring.pre_contact_threshold))
        pre.setToolTip("接触の手前（予兆）で固定し始める距離です。")
        pre.valueChanged.connect(lambda v: self._set_value("control.cursor_anchoring.pre_contact_threshold", float(v)))
        layout.addRow("固定開始（予兆）しきい値", pre)

        ff = QSpinBox()
        ff.setRange(0, 30)
        ff.setValue(int(s.control.cursor_anchoring.freeze_frames))
        ff.setToolTip("接触中＋直後に固定するフレーム数です。増やすとズレは減りますが操作が重くなります。")
        ff.valueChanged.connect(lambda v: self._set_value("control.cursor_anchoring.freeze_frames", int(v)))
        layout.addRow("固定フレーム数", ff)

        ov = QDoubleSpinBox()
        ov.setRange(0.0, 1.0)
        ov.setSingleStep(0.01)
        ov.setValue(float(s.control.cursor_anchoring.override_smoothing_factor_ema))
        ov.setToolTip("固定中のEMA係数です。0に近いほどほぼ固定、1に近いほど追従します。")
        ov.valueChanged.connect(lambda v: self._set_value("control.cursor_anchoring.override_smoothing_factor_ema", float(v)))
        layout.addRow("固定中EMA係数", ov)

        return g

    def _group_scroll(self) -> QGroupBox:
        g = QGroupBox("操作（スクロール）")
        layout = QFormLayout(g)
        s = self._store.get()

        ss = QSpinBox()
        ss.setRange(1, 5000)
        ss.setValue(int(s.control.scroll_sensitivity))
        ss.setToolTip("スクロール速度（移動量の倍率）です。大きいほど速くスクロールします。")
        ss.valueChanged.connect(lambda v: self._set_value("control.scroll_sensitivity", int(v)))
        layout.addRow("スクロール感度", ss)

        sd = QDoubleSpinBox()
        sd.setRange(0.0, 0.05)
        sd.setSingleStep(0.001)
        sd.setValue(float(s.control.scroll_deadzone))
        sd.setToolTip("スクロール開始の死域です。小さな上下ブレによる勝手スクロールを防ぎます。")
        sd.valueChanged.connect(lambda v: self._set_value("control.scroll_deadzone", float(v)))
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
        layout.addRow("保存先", dir_label)

        file_label = QLabel(str(s.logging.log_file_name))
        file_label.setToolTip("ログファイル名です（現在はGUIから編集不可）。")
        layout.addRow("ファイル名", file_label)

        mb = QSpinBox()
        mb.setRange(1024, 1024 * 1024 * 1024)
        mb.setSingleStep(1024 * 1024)
        mb.setValue(int(s.logging.max_bytes))
        mb.setToolTip("このサイズ（bytes）を超えたらローテーションします。")
        mb.valueChanged.connect(lambda v: self._set_value("logging.max_bytes", int(v)))
        layout.addRow("最大サイズ（bytes）", mb)

        bc = QSpinBox()
        bc.setRange(0, 100)
        bc.setValue(int(s.logging.backup_count))
        bc.setToolTip("保持するバックアップ数です。")
        bc.valueChanged.connect(lambda v: self._set_value("logging.backup_count", int(v)))
        layout.addRow("バックアップ数", bc)

        fl = QCheckBox()
        fl.setChecked(bool(s.logging.flush))
        fl.setToolTip("ONにすると各イベントを即時flushします（研究ログの欠落を防止）。")
        fl.stateChanged.connect(lambda st: self._set_value("logging.flush", bool(st)))
        layout.addRow("即時flush", fl)

        return g

    def _set_value(self, dotted: str, value: Any) -> None:
        try:
            self._store.set_value(dotted, value)
        except Exception:
            pass

    def _set_and_restart(self, dotted: str, value: Any) -> None:
        self._set_value(dotted, value)
        self.requestRestartCamera.emit()

    # -----------------------------
    # PieMenu settings tab
    # -----------------------------
    def _build_pie_menu_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(0, 0, 0, 0)

        info = QLabel(
            "Preset 1 / 3 はユーザー設定です。\n"
            "Type=Shortcut は PyAutoGUI 形式（例: ctrl+shift+p / cmd+space）を想定します。"
        )
        info.setWordWrap(True)
        root.addWidget(info)

        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addWidget(self._group_pie_preset("Preset 1 (Custom)", preset_key="custom_1"))
        lay.addWidget(self._group_pie_preset("Preset 3 (Custom)", preset_key="custom_3"))
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)
        return w

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
            label_edit.textChanged.connect(lambda v, p=label_path: self._set_value(p, str(v)))
            row_lay.addWidget(label_edit, 2)

            typ = QComboBox()
            typ.addItem("Shortcut (Key)", "shortcut")
            typ.addItem("Application (Path)", "application")
            cur_idx = typ.findData(str(slot.type))
            typ.setCurrentIndex(cur_idx if cur_idx >= 0 else 0)
            type_path = f"pie_menu.presets.{preset_key}.slots.{i}.type"
            typ.currentIndexChanged.connect(lambda _, cb=typ, p=type_path: self._set_value(p, str(cb.currentData())))
            row_lay.addWidget(typ, 1)

            value_edit = QLineEdit(str(slot.value))
            value_edit.setPlaceholderText("Value")
            value_path = f"pie_menu.presets.{preset_key}.slots.{i}.value"
            value_edit.textChanged.connect(lambda v, p=value_path: self._set_value(p, str(v)))
            row_lay.addWidget(value_edit, 3)

            browse = QPushButton("参照…")

            def _browse_into_value(*, ve: QLineEdit, p: str) -> None:
                try:
                    path, _ = QFileDialog.getOpenFileName(self, "アプリ/ファイルを選択")
                    if not path:
                        return
                    ve.setText(path)
                    self._set_value(p, str(path))
                except Exception:
                    pass

            browse.clicked.connect(lambda _, ve=value_edit, p=value_path: _browse_into_value(ve=ve, p=p))
            row_lay.addWidget(browse)

            form.addRow(f"Slot {i}", row)

        return g

    # -----------------------------
    # Motion tab
    # -----------------------------
    def _build_motion_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

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

