"""macOS 向けのオーバーレイウィンドウ補助。

ブラウザ等の「ネイティブフルスクリーン」は専用スペースになるため、通常の Qt ウィンドウは
そのスペース上に載らず PieMenu が見えなくなることがある。

NSWindow の collectionBehavior に FullScreenAuxiliary / CanJoinAllSpaces を足すと、
フルスクリーンアプリと同じスペースに補助ウィンドウとして載せられる場合がある。
"""

from __future__ import annotations

import sys
from typing import Any


def apply_fullscreen_auxiliary_collection_behavior(widget: Any) -> None:
    """QWidget の背後にある NSWindow に、フルスクリーン空間へ追従する振る舞いを付与する。"""

    if sys.platform != "darwin":
        return
    try:
        from ctypes import c_void_p

        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
        )
        from objc import objc_object
    except ImportError:
        return

    try:
        wid = int(widget.winId())
    except Exception:
        return
    if wid == 0:
        return

    try:
        ns_view = objc_object(c_void_p=c_void_p(wid))
        win = ns_view.window()
        if win is None:
            return
        extra = int(NSWindowCollectionBehaviorCanJoinAllSpaces) | int(
            NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        cur = int(win.collectionBehavior())
        win.setCollectionBehavior_(cur | extra)
    except Exception:
        # 将来の Qt / macOS 差分では失敗し得るため、表示不能より無視して継続する。
        return
