from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .config_loader import ChangeEvent, ConfigStore, LoggingConfig, Settings


def _now_ms() -> int:
    return int(time.time() * 1000)


def _timestamp_suffix() -> str:
    # 例: 20260408-123045
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def _safe_json(obj: Any) -> Any:
    """JSON化しやすい値へ寄せる。

    研究用途のログは「後で解析しやすいこと」が主目的なので、複雑なオブジェクトは
    できるだけ壊れない形（dict/str）に落として残す。
    """

    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_json(v) for k, v in obj.items()}
    if is_dataclass(obj):
        return _safe_json(asdict(obj))
    return str(obj)


@dataclass(frozen=True)
class LogRecord:
    """ログ1件（1行=1JSON）を表す。

    最新が上位（先頭）に来る運用のため、時系列の復元は `ts_ms` で行えるようにしておく。
    """

    ts_ms: int
    level: str
    event: str
    data: Dict[str, Any]


class JsonPrependLogger:
    """JSON Linesログを「最新が先頭」になるように管理するロガー。

    仕様（spec 5.3）:
    - JSON形式で記録
    - 最新データが上位（先頭）
    - 10MB超でローテーション

    設計上の注意:
    - 先頭追記は本質的にファイル全体の書き換えが必要で、巨大ログには向かない。
      ただし研究用途の初期段階では「読みやすさ・解析しやすさ」を優先し、単純実装とする。
    """

    def __init__(self, logging_config: LoggingConfig) -> None:
        self._lock = threading.RLock()
        self._cfg = logging_config
        self._path = self._build_log_path(logging_config)

    @property
    def path(self) -> Path:
        return self._path

    def reconfigure(self, logging_config: LoggingConfig) -> None:
        """出力設定を更新する（例: log_dir変更）。"""

        with self._lock:
            self._cfg = logging_config
            self._path = self._build_log_path(logging_config)

    def write(self, level: str, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        """ログを書き込む。

        失敗してもアプリ本体を止めない（研究・検証中の作業を優先）方針とする。
        """

        record = LogRecord(ts_ms=_now_ms(), level=str(level), event=str(event), data=_safe_json(data or {}))
        try:
            self._write_record(record)
        except Exception:
            # ログの失敗で主要処理が止まらないようにする。
            print("JsonPrependLogger: ログ書き込みに失敗しました", file=sys.stderr)

    def _write_record(self, record: LogRecord) -> None:
        line = json.dumps(_safe_json(asdict(record)), ensure_ascii=False) + "\n"
        encoded_len = len(line.encode("utf-8"))

        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)

            # 書き込み前にローテーションを判定する（新規行を足した後に超える場合も含む）
            current_size = self._path.stat().st_size if self._path.exists() else 0
            if current_size + encoded_len > int(self._cfg.max_bytes):
                self._rotate_locked()

            # 最新が先頭になるよう、(new_line + old_content) で再生成する。
            old = self._path.read_text(encoding="utf-8") if self._path.exists() else ""
            self._path.write_text(line + old, encoding="utf-8")
            if bool(self._cfg.flush):
                # write_textは内部でcloseされるため通常flush不要だが、意図を明示しておく。
                pass

    def _rotate_locked(self) -> None:
        if not self._path.exists():
            return

        rotated = self._path.with_name(f"{self._path.name}.bak.{_timestamp_suffix()}")
        os.replace(self._path, rotated)

        # backup_count を超える古いバックアップは削除
        backups = sorted(
            self._path.parent.glob(f"{self._path.name}.bak.*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        keep = int(self._cfg.backup_count)
        for p in backups[keep:]:
            try:
                p.unlink()
            except Exception:
                # 削除できない場合も本体は継続
                pass

    @staticmethod
    def _build_log_path(cfg: LoggingConfig) -> Path:
        return Path(cfg.log_dir) / cfg.log_file_name


class LoggingManager:
    """ConfigStoreと連携してロガー設定変更に追従する薄いラッパ。

    目的:
    - ロガーの再初期化条件（logging設定変更）をConfigStoreの通知（important change）に寄せる。
    - アプリ側は `manager.logger.write(...)` だけで済むようにする。
    """

    def __init__(self, store: ConfigStore) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._logger = JsonPrependLogger(store.get().logging)

        # logging.* はConfigStore側で重要変更扱いにしているため、通知を受けて追従できる。
        self._unsubscribe = store.subscribe(self._on_change)

    @property
    def logger(self) -> JsonPrependLogger:
        return self._logger

    def close(self) -> None:
        """購読解除。長寿命プロセスでは終了処理で呼ぶ。"""

        with self._lock:
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None

    def _on_change(self, event: ChangeEvent, settings: Settings) -> None:
        if "logging" not in event.top_level_sections:
            return
        with self._lock:
            self._logger.reconfigure(settings.logging)

