from __future__ import annotations

import copy
import dataclasses
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml


@dataclass(frozen=True)
class CameraROI:
    """操作有効エリア(ROI)の設定。

    研究用途では、カメラ画像の全域を使うよりも中心部だけを使った方が、
    - 少ない手の移動で画面全域をカバーできる
    - 端部ノイズや背景の影響を受けにくい
    という理由で安定しやすい。
    """

    enabled: bool
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class CameraConfig:
    """カメラ取得に関する設定。"""

    device_id: int
    width: int
    height: int
    fps: int
    roi: CameraROI


@dataclass(frozen=True)
class DetectionConfig:
    """MediaPipe検出に関する設定。"""

    min_detection_confidence: float
    min_tracking_confidence: float
    frame_skip: int


@dataclass(frozen=True)
class CursorAnchoringConfig:
    """クリック時の座標固定(カーソルアンカリング)設定。

    親指を接触させる動作は微細な手ブレを生みやすく、UI上の小さなボタンを押す際に
    意図せずカーソルがずれて失敗しやすい。接触予兆〜接触中の短い区間だけカーソル更新を抑えることで、
    クリック精度を優先する。
    """

    enabled: bool
    pre_contact_threshold: float
    freeze_frames: int
    override_smoothing_factor_ema: float


@dataclass(frozen=True)
class ControlConfig:
    """OS制御（マウス等）の設定。"""

    sensitivity: float
    sensitivity_x: float
    sensitivity_y: float
    # 互換性: 旧キー smoothing_factor_ema から smoothing_factor へ移行
    smoothing_factor: float
    click_threshold: float
    tap_interval_ms: int
    mouse_mode_stable_frames: int
    cursor_anchoring: CursorAnchoringConfig


@dataclass(frozen=True)
class LoggingConfig:
    """ログ出力設定。"""

    log_dir: str
    log_file_name: str
    max_bytes: int
    backup_count: int
    flush: bool


@dataclass(frozen=True)
class Settings:
    """設定の不変スナップショット。

    重要な意図:
    - 読み手は常に `ConfigStore.get()` でスナップショットを受け取り、処理中に値が変わっても破綻しない。
    - 書き手は更新のたびにスナップショットを丸ごと差し替える（ロック範囲を最小化しやすい）。
    """

    camera: CameraConfig
    detection: DetectionConfig
    control: ControlConfig
    logging: LoggingConfig


@dataclass(frozen=True)
class ChangeEvent:
    """設定変更イベント。

    ハイブリッド設計の意図:
    - 読み手は通知がなくても `get()` を呼べば最新を参照できる（pull）。
    - ただし、カメラ再初期化など「再構築が必要な変更」は通知で即応した方が安全（push）。
    """

    changed_paths: Tuple[str, ...]
    timestamp_ms: int

    @property
    def top_level_sections(self) -> Tuple[str, ...]:
        """変更が含まれる最上位セクション名（camera/detection/control/logging）。"""

        sections: List[str] = []
        for p in self.changed_paths:
            head = p.split(".", 1)[0]
            if head not in sections:
                sections.append(head)
        return tuple(sections)


Subscriber = Callable[[ChangeEvent, Settings], None]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _deep_get(d: Dict[str, Any], path: Sequence[str]) -> Any:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            raise KeyError(".".join(path))
        cur = cur[k]
    return cur


def _deep_set(d: Dict[str, Any], path: Sequence[str], value: Any) -> None:
    cur: Any = d
    for k in path[:-1]:
        nxt = cur.get(k)
        if nxt is None:
            cur[k] = {}
            nxt = cur[k]
        if not isinstance(nxt, dict):
            raise TypeError(f"設定パスの途中がdictではありません: {'.'.join(path)}")
        cur = nxt
    cur[path[-1]] = value


def _as_float(v: Any, *, name: str) -> float:
    try:
        return float(v)
    except Exception as e:
        raise ValueError(f"{name} はfloatに変換できる必要があります: {v!r}") from e


def _as_int(v: Any, *, name: str) -> int:
    try:
        return int(v)
    except Exception as e:
        raise ValueError(f"{name} はintに変換できる必要があります: {v!r}") from e


def _as_bool(v: Any, *, name: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.lower() in ("true", "false", "0", "1", "yes", "no"):
        return v.lower() in ("true", "1", "yes")
    raise ValueError(f"{name} はboolに変換できる必要があります: {v!r}")


def _validate_roi(roi: Dict[str, Any]) -> CameraROI:
    enabled = _as_bool(roi.get("enabled", True), name="camera.roi.enabled")
    x = _as_float(roi.get("x", 0.0), name="camera.roi.x")
    y = _as_float(roi.get("y", 0.0), name="camera.roi.y")
    w = _as_float(roi.get("w", 1.0), name="camera.roi.w")
    h = _as_float(roi.get("h", 1.0), name="camera.roi.h")
    for name, val in (("x", x), ("y", y), ("w", w), ("h", h)):
        if not (0.0 <= val <= 1.0):
            raise ValueError(f"camera.roi.{name} は0.0〜1.0である必要があります: {val}")
    if x + w > 1.0 or y + h > 1.0:
        raise ValueError("camera.roi は画像範囲(0..1)からはみ出しています")
    return CameraROI(enabled=enabled, x=x, y=y, w=w, h=h)


def _validate_settings_dict(raw: Dict[str, Any]) -> Settings:
    """YAML辞書からSettingsスナップショットへ変換しつつ検証する。

    研究用途では「パラメータが壊れていたら早めに気づける」ことが重要なので、
    ここで最低限の型/範囲チェックを行う。
    """

    camera = raw.get("camera", {})
    detection = raw.get("detection", {})
    control = raw.get("control", {})
    logging = raw.get("logging", {})

    roi = _validate_roi(camera.get("roi", {}))
    camera_cfg = CameraConfig(
        device_id=_as_int(camera.get("device_id", 0), name="camera.device_id"),
        width=_as_int(camera.get("width", 640), name="camera.width"),
        height=_as_int(camera.get("height", 480), name="camera.height"),
        fps=_as_int(camera.get("fps", 30), name="camera.fps"),
        roi=roi,
    )

    det_cfg = DetectionConfig(
        min_detection_confidence=_as_float(
            detection.get("min_detection_confidence", 0.6),
            name="detection.min_detection_confidence",
        ),
        min_tracking_confidence=_as_float(
            detection.get("min_tracking_confidence", 0.6),
            name="detection.min_tracking_confidence",
        ),
        frame_skip=_as_int(detection.get("frame_skip", 0), name="detection.frame_skip"),
    )

    anchoring_raw = control.get("cursor_anchoring", {})
    anchoring_cfg = CursorAnchoringConfig(
        enabled=_as_bool(anchoring_raw.get("enabled", True), name="control.cursor_anchoring.enabled"),
        pre_contact_threshold=_as_float(
            anchoring_raw.get("pre_contact_threshold", 0.05),
            name="control.cursor_anchoring.pre_contact_threshold",
        ),
        freeze_frames=_as_int(
            anchoring_raw.get("freeze_frames", 3),
            name="control.cursor_anchoring.freeze_frames",
        ),
        override_smoothing_factor_ema=_as_float(
            anchoring_raw.get("override_smoothing_factor_ema", 0.05),
            name="control.cursor_anchoring.override_smoothing_factor_ema",
        ),
    )

    # smoothing_factor は新キー。旧キー smoothing_factor_ema も許容する。
    smoothing_raw = (
        control.get("smoothing_factor")
        if "smoothing_factor" in control
        else control.get("smoothing_factor_ema", 0.35)
    )
    smoothing_name = "control.smoothing_factor" if "smoothing_factor" in control else "control.smoothing_factor_ema"

    ctl_cfg = ControlConfig(
        sensitivity=_as_float(control.get("sensitivity", 1.0), name="control.sensitivity"),
        sensitivity_x=_as_float(
            control.get("sensitivity_x", control.get("sensitivity", 1.0)), name="control.sensitivity_x"
        ),
        sensitivity_y=_as_float(
            control.get("sensitivity_y", control.get("sensitivity", 1.0)), name="control.sensitivity_y"
        ),
        smoothing_factor=_as_float(smoothing_raw, name=smoothing_name),
        click_threshold=_as_float(control.get("click_threshold", 0.035), name="control.click_threshold"),
        tap_interval_ms=_as_int(control.get("tap_interval_ms", 300), name="control.tap_interval_ms"),
        mouse_mode_stable_frames=_as_int(
            control.get("mouse_mode_stable_frames", 6), name="control.mouse_mode_stable_frames"
        ),
        cursor_anchoring=anchoring_cfg,
    )

    log_cfg = LoggingConfig(
        log_dir=str(logging.get("log_dir", "logs")),
        log_file_name=str(logging.get("log_file_name", "events.jsonl")),
        max_bytes=_as_int(logging.get("max_bytes", 10 * 1024 * 1024), name="logging.max_bytes"),
        backup_count=_as_int(logging.get("backup_count", 10), name="logging.backup_count"),
        flush=_as_bool(logging.get("flush", True), name="logging.flush"),
    )

    return Settings(camera=camera_cfg, detection=det_cfg, control=ctl_cfg, logging=log_cfg)


class ConfigStore:
    """YAML設定の読み書きと、実行中反映を担う設定ストア。

    このクラスは「研究用途での調整容易性」と「実運用の安全性」の両立を狙う。
    - 読み手: `get()` で常に最新スナップショットを取得（pull）。
    - 書き手: `update()`/`set_value()` で変更し、YAMLへ永続化（atomic write）。
    - 重要変更: 例) カメラ解像度/FPS変更のように再初期化が必要なものは購読者へ通知（push）。
    """

    def __init__(self, settings_path: str | os.PathLike[str] = "config/settings.yaml") -> None:
        self._settings_path = Path(settings_path)
        self._lock = threading.RLock()
        self._subscribers: List[Subscriber] = []
        self._raw: Dict[str, Any] = {}
        self._settings: Optional[Settings] = None

        self.reload_from_disk()

    @property
    def settings_path(self) -> Path:
        return self._settings_path

    def get(self) -> Settings:
        """最新スナップショットを返す。"""

        with self._lock:
            if self._settings is None:
                # 通常は発生しないが、初期化中や例外時の安全策としてロードを試みる。
                self.reload_from_disk()
            assert self._settings is not None
            return self._settings

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """設定変更を購読する。

        返り値は購読解除関数。GUIなど寿命があるオブジェクトは必ず解除できるようにしておくと安全。
        """

        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def reload_from_disk(self) -> None:
        """ディスク上のsettings.yamlから再読込する。

        目的:
        - GUI以外の手段（手動編集、別ツール）でYAMLが変更された場合にも追従できる。
        """

        with self._lock:
            if not self._settings_path.exists():
                raise FileNotFoundError(f"settings.yamlが見つかりません: {self._settings_path}")
            text = self._settings_path.read_text(encoding="utf-8")
            raw = yaml.safe_load(text) or {}
            if not isinstance(raw, dict):
                raise ValueError("settings.yamlの最上位はdictである必要があります")
            settings = _validate_settings_dict(raw)
            self._raw = raw
            self._settings = settings

    def update(self, patch: Dict[str, Any]) -> ChangeEvent:
        """複数の設定値を一括更新する。

        patch例:
        - {"camera": {"width": 1280, "height": 720}}
        - {"control": {"sensitivity": 1.2}}
        """

        changed_paths: List[str] = []
        with self._lock:
            before = copy.deepcopy(self._raw)
            self._merge_dict(self._raw, patch, changed_paths=changed_paths)
            # validate and snapshot swap
            settings = _validate_settings_dict(self._raw)
            self._settings = settings
            self._atomic_write_yaml(self._raw)

            event = ChangeEvent(changed_paths=tuple(sorted(set(changed_paths))), timestamp_ms=_now_ms())
            self._notify_if_important(event, settings)
            return event

    def set_value(self, dotted_path: str, value: Any) -> ChangeEvent:
        """1つの設定値を更新する（dotted path指定）。

        例:
        - set_value(\"camera.width\", 640)
        - set_value(\"control.cursor_anchoring.enabled\", False)
        """

        parts = dotted_path.split(".")
        if not parts or any(not p for p in parts):
            raise ValueError(f"dotted_pathが不正です: {dotted_path!r}")

        with self._lock:
            _deep_set(self._raw, parts, value)
            settings = _validate_settings_dict(self._raw)
            self._settings = settings
            self._atomic_write_yaml(self._raw)

            event = ChangeEvent(changed_paths=(dotted_path,), timestamp_ms=_now_ms())
            self._notify_if_important(event, settings)
            return event

    def as_dict(self) -> Dict[str, Any]:
        """現在の設定を辞書として返す（GUIのフォーム生成などに使える）。"""

        with self._lock:
            return copy.deepcopy(self._raw)

    def _atomic_write_yaml(self, raw: Dict[str, Any]) -> None:
        """YAMLを原子的に保存する。

        意図:
        - 書き込み途中でプロセスが落ちても、ファイルが壊れにくい。
        - 設定は研究データの再現性に直結するため、保存の堅牢性を優先する。
        """

        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        content = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        tmp_dir = str(self._settings_path.parent)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=tmp_dir, delete=False) as tf:
            tf.write(content)
            tf.flush()
            os.fsync(tf.fileno())
            tmp_path = Path(tf.name)
        os.replace(tmp_path, self._settings_path)

    def _notify_if_important(self, event: ChangeEvent, settings: Settings) -> None:
        """重要変更のみ購読者に通知する。

        重要変更の基準:
        - camera.* は再初期化が必要になる可能性が高いので常に重要
        - logging.* は出力先/ローテーション等が変わるため重要
        - その他は将来的に追加していく（研究の進捗に合わせて）
        """

        important_sections = {"camera", "logging"}
        if not any(sec in important_sections for sec in event.top_level_sections):
            return

        # コールバック中にsubscribe/unsubscribeが走っても破綻しないよう、コピーを取る。
        with self._lock:
            subscribers = list(self._subscribers)
        for cb in subscribers:
            try:
                cb(event, settings)
            except Exception:
                # 設定変更通知は補助的な仕組みなので、例外で本体を落とさない。
                pass

    @staticmethod
    def _merge_dict(target: Dict[str, Any], patch: Dict[str, Any], *, changed_paths: List[str], prefix: str = "") -> None:
        for k, v in patch.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and isinstance(target.get(k), dict):
                ConfigStore._merge_dict(target[k], v, changed_paths=changed_paths, prefix=path)
            else:
                target[k] = v
                changed_paths.append(path)


_DEFAULT_STORE: Optional[ConfigStore] = None


def get_config_store(settings_path: str | os.PathLike[str] = "config/settings.yaml") -> ConfigStore:
    """アプリ全体で共有しやすいConfigStoreを返す。

    研究用途での意図:
    - スクリプト（testsや検証用ツール）からも簡単に利用できる入口を用意する。
    - ただしテスト容易性の観点から、必要に応じて `ConfigStore()` を直接生成することも許容する。
    """

    global _DEFAULT_STORE
    if _DEFAULT_STORE is None or _DEFAULT_STORE.settings_path != Path(settings_path):
        _DEFAULT_STORE = ConfigStore(settings_path=settings_path)
    return _DEFAULT_STORE

