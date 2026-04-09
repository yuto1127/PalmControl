from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMainWindow,
)

from src.gui.worker import VisionControlWorker
from src.utils.config_loader import ConfigStore


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

        self.setWindowTitle("PalmControl")
        self.resize(980, 720)

        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        self._settings_tab = self._build_settings_tab()
        self._motion_tab = self._build_motion_tab()
        self._log_tab = self._build_log_tab()
        self._manual_tab = self._build_manual_tab()

        self._tabs.addTab(self._settings_tab, "設定")
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

        form.addWidget(self._group_camera())
        form.addWidget(self._group_roi())
        form.addWidget(self._group_detection())
        form.addWidget(self._group_control())
        form.addWidget(self._group_anchoring())
        form.addWidget(self._group_scroll())
        form.addWidget(self._group_logging())
        form.addStretch(1)

        root.addWidget(form_container)
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
    # Motion tab
    # -----------------------------
    def _build_motion_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)

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

        self._frame_label = QLabel()
        self._frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame_label.setMinimumHeight(480)
        root.addWidget(self._frame_label, 1)

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
        pix = QPixmap.fromImage(qimg)
        self._frame_label.setPixmap(pix.scaled(self._frame_label.size(), Qt.AspectRatioMode.KeepAspectRatio))

    def _on_status_ready(self, status: dict) -> None:
        try:
            self._status_label.setText(
                f"mode={status.get('mode')} contact={status.get('contact')} "
                f"dist={status.get('contact_distance')} "
                f"lat={status.get('latency_ms'):.1f}ms fps={status.get('fps'):.1f}"
            )
        except Exception:
            self._status_label.setText(str(status))

    def _on_worker_error(self, msg: str) -> None:
        self._status_label.setText(f"error: {msg}")

    def _on_tab_changed(self, idx: int) -> None:
        # モーションタブが見えている時だけプレビューをONにする
        is_motion = self._tabs.tabText(idx) == "モーションテスト"
        self.previewEnabledChanged.emit(bool(is_motion and self._preview_cb.isChecked()))

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
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(self._load_manual_text())
        root.addWidget(view, 1)
        return w

    def _load_manual_text(self) -> str:
        docs_dir = Path("docs")
        if not docs_dir.exists():
            return "docs/ が見つかりません。操作ガイドは docs/ に追加してください。"
        # 最小: docs/ 内の *.md を連結して表示
        md_files = sorted(docs_dir.glob("*.md"))
        if not md_files:
            return "docs/ にMarkdownがありません。操作ガイドを追加してください。"
        parts = []
        for p in md_files:
            try:
                parts.append(f"=== {p.name} ===\n{p.read_text(encoding='utf-8')}")
            except Exception:
                pass
        return "\n\n".join(parts) if parts else "マニュアルを読み込めませんでした。"

