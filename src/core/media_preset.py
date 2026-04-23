from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class MediaAction:
    """Preset 2（Media）で実行される固定アクション。

    type/value は CommandExecutor に渡す想定の表現。
    """

    id: str
    label: str
    type: str  # "shortcut"
    value: str


# YouTube用途を優先しつつ、音量だけはOS側（macOSはosascript）で変更する構成
MEDIA_ACTIONS: Tuple[MediaAction, ...] = (
    MediaAction(id="os_vol_up", label="Vol +", type="shortcut", value="volumeup"),
    MediaAction(id="os_vol_down", label="Vol -", type="shortcut", value="volumedown"),
    MediaAction(id="yt_next", label="Next", type="shortcut", value="shift+n"),
    MediaAction(id="yt_prev", label="Prev", type="shortcut", value="shift+p"),
    MediaAction(id="yt_play_pause", label="Play/Pause", type="shortcut", value="k"),
    MediaAction(id="os_mute", label="Mute", type="shortcut", value="volumemute"),
    MediaAction(id="yt_back_10", label="-10s", type="shortcut", value="j"),
    MediaAction(id="yt_fwd_10", label="+10s", type="shortcut", value="l"),
)


def media_actions_by_id() -> Dict[str, MediaAction]:
    return {a.id: a for a in MEDIA_ACTIONS}


def default_media_layout() -> Tuple[str, ...]:
    """デフォルトのスロット配置（8個）。"""

    return tuple(a.id for a in MEDIA_ACTIONS)


def validate_media_layout(ids: List[str]) -> Tuple[str, ...]:
    """Preset 2 の配置を検証して正規化する。

    - 長さ8
    - 既知IDのみ
    - 重複は許可（同じ操作を複数スロットへ置きたいケース）
    """

    if len(ids) != 8:
        raise ValueError("pie_menu.preset2_layout は長さ8である必要があります")
    known = media_actions_by_id()
    out: List[str] = []
    for i, x in enumerate(ids, start=1):
        s = str(x).strip()
        if s not in known:
            raise ValueError(f"pie_menu.preset2_layout[{i}] が不正です: {s!r}")
        out.append(s)
    return tuple(out)

