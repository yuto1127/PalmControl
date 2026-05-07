"""macOS 向けのオーバーレイウィンドウ補助。

ブラウザ等の「ネイティブフルスクリーン」は専用スペースになるため、通常の Qt ウィンドウは
そのスペース上に載らず PieMenu が見えなくなることがある。

NSWindow の collectionBehavior に FullScreenAuxiliary / CanJoinAllSpaces を足すと、
フルスクリーンアプリと同じスペースに補助ウィンドウとして載せられる場合がある。
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List, Optional

_PANEL_SINGLETON: Any | None = None
_PANEL_LABEL: Any | None = None
_PIE_VIEW: Any | None = None
PalmPieNativeView: Any = None
PalmPanelPassThroughContentView: Any = None

if sys.platform == "darwin":
    try:
        import objc  # type: ignore[import-not-found]
        from AppKit import (  # type: ignore[import-not-found]
            NSBezierPath,
            NSColor,
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSGraphicsContext,
            NSMutableParagraphStyle,
            NSParagraphStyleAttributeName,
            NSView,
            NSWorkspace,
        )
        from Foundation import NSMakeRect, NSString  # type: ignore[import-not-found]

        def _ns_draw_string_in_rect(text: str, rect: Any, attrs: Dict[str, Any]) -> None:
            """PyObjC では Python str に drawInRect が無いことがあるため NSString 経由で描画する。"""

            NSString.stringWithString_(str(text)).drawInRect_withAttributes_(rect, attrs)

        def _ns_draw_app_icon_at(path: str, cx: float, ty: float, size: float) -> None:
            if not path:
                return
            try:
                p = os.path.realpath(os.path.expanduser(str(path).strip()))
            except Exception:
                p = str(path).strip()
            # .app はパッケージ（ディレクトリ）のため isfile だと常に失敗する
            if not os.path.exists(p):
                return
            img = NSWorkspace.sharedWorkspace().iconForFile_(p)
            if img is None:
                return
            sz = img.size()
            iw = float(size)
            ih = float(size)
            dst = NSMakeRect(cx - iw / 2.0, ty - 40.0, iw, ih)
            src = NSMakeRect(0.0, 0.0, float(sz.width), float(sz.height))
            try:
                from AppKit import NSCompositingOperationSourceOver  # type: ignore[import-not-found]

                op = int(NSCompositingOperationSourceOver)
            except Exception:
                op = 2
            try:
                img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(dst, src, op, 1.0, True, None)
            except Exception:
                try:
                    img.setSize_((iw, ih))
                    img.drawInRect_(dst)
                except Exception:
                    pass

        class PalmPanelPassThroughContentView(NSView):  # type: ignore[no-redef]
            """マウスヒットを常にスキーし、下のアプリへイベントを渡す。"""

            def isFlipped(self) -> bool:  # noqa: N802
                return True

            def hitTest_(self, point):  # noqa: N802
                return None

        class PalmPieNativeView(NSView):  # type: ignore[no-redef]
            """Qt PieMenu に近い見た目を AppKit で描画（全画面動画上の NSPanel 用）。"""

            def isFlipped(self) -> bool:  # noqa: N802
                return True

            def initWithFrame_(self, frame):  # noqa: N802
                self = objc.super(PalmPieNativeView, self).initWithFrame_(frame)
                if self is None:
                    return None
                self._banner_only = True
                self._banner_text = "PieMenu overlay active"
                self._labels: List[str] = [f"Slot {i}" for i in range(1, 9)]
                self._preset_title = "Preset 1"
                self._selection_slot: Optional[int] = None
                self._subtitle = ""
                self._action_msg = ""
                self._show_click = False
                self._icon_paths: List[str] = [""] * 8
                return self

            def hitTest_(self, point):  # noqa: N802
                """マウスを下のウィンドウへ通す（オーバーレイは見えるだけ）。"""

                return None

            def applyPieState_(self, state: Dict[str, Any]) -> None:  # noqa: N802
                self._banner_only = False
                labels = state.get("labels") or []
                self._labels = [str(x) for x in list(labels)[:8]]
                while len(self._labels) < 8:
                    self._labels.append("—")
                raw_icons = state.get("icon_paths") or []
                self._icon_paths = []
                for i in range(8):
                    if i < len(raw_icons) and raw_icons[i]:
                        self._icon_paths.append(str(raw_icons[i]).strip())
                    else:
                        self._icon_paths.append("")
                while len(self._icon_paths) < 8:
                    self._icon_paths.append("")
                self._preset_title = str(state.get("preset_title") or "")
                sel = state.get("selection_slot")
                self._selection_slot = int(sel) if sel is not None else None
                self._subtitle = str(state.get("subtitle") or "")
                self._action_msg = str(state.get("action_msg") or "")
                self._show_click = bool(state.get("show_click"))
                self.setNeedsDisplay_(True)

            def applyBanner_(self, text: str) -> None:  # noqa: N802
                self._banner_only = True
                self._banner_text = str(text)
                self.setNeedsDisplay_(True)

            def drawRect_(self, rect) -> None:  # noqa: N802
                try:
                    NSGraphicsContext.currentContext().setShouldAntialias_(True)
                except Exception:
                    pass
                bounds = self.bounds()
                w = float(bounds.size.width)
                h = float(bounds.size.height)
                if w < 8.0 or h < 8.0:
                    return

                if self._banner_only:
                    NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.75).setFill()
                    NSBezierPath.fillRect_(bounds)
                    font = NSFont.boldSystemFontOfSize_(14.0)
                    color = NSColor.whiteColor()
                    para = NSMutableParagraphStyle.alloc().init()
                    try:
                        para.setAlignment_(1)
                    except Exception:
                        pass
                    attrs = {
                        NSFontAttributeName: font,
                        NSForegroundColorAttributeName: color,
                        NSParagraphStyleAttributeName: para,
                    }
                    _ns_draw_string_in_rect(str(self._banner_text), NSMakeRect(8.0, 8.0, w - 16.0, h - 16.0), attrs)
                    return

                cx = w / 2.0
                cy = h / 2.0
                radius = min(w, h) * 0.45
                inner_r = radius * 0.42

                bg = NSColor.colorWithCalibratedRed_green_blue_alpha_(20.0 / 255.0, 20.0 / 255.0, 20.0 / 255.0, 150.0 / 255.0)
                bg.setFill()
                NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - radius, cy - radius, radius * 2.0, radius * 2.0)).fill()

                for i in range(8):
                    slot = i + 1
                    is_sel = self._selection_slot is not None and int(self._selection_slot) == int(slot)
                    center_deg = float(i * 45.0)
                    if is_sel:
                        fill = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 140.0 / 255.0, 1.0, 110.0 / 255.0)
                        fill.setFill()
                        start_deg = float(center_deg - 22.5)
                        end_deg = start_deg + 45.0
                        # NSBezierPath の arc は isFlipped 環境で角度の向きがズレることがあるため、
                        # Qt と同じく cos/sin の点列で扇形を描いて選択とハイライトを一致させる。
                        wedge = NSBezierPath.bezierPath()
                        wedge.moveToPoint_((cx, cy))
                        steps = 16
                        for s in range(steps + 1):
                            t = float(s) / float(steps)
                            deg = float(start_deg + (end_deg - start_deg) * t)
                            ang = math.radians(deg)
                            x2 = cx + math.cos(ang) * radius
                            y2 = cy - math.sin(ang) * radius
                            wedge.lineToPoint_((x2, y2))
                        wedge.closePath()
                        wedge.fill()

                    mid = math.radians(center_deg)
                    tx = cx + math.cos(mid) * (radius * 0.72)
                    ty = cy - math.sin(mid) * (radius * 0.72)
                    text = str(self._labels[i] if i < len(self._labels) else f"Slot {slot}")
                    font = NSFont.boldSystemFontOfSize_(11.0) if is_sel else NSFont.systemFontOfSize_(10.0)
                    para = NSMutableParagraphStyle.alloc().init()
                    try:
                        para.setAlignment_(1)
                    except Exception:
                        pass
                    tcol = NSColor.colorWithCalibratedWhite_alpha_(1.0, 220.0 / 255.0)
                    attrs = {
                        NSFontAttributeName: font,
                        NSForegroundColorAttributeName: tcol,
                        NSParagraphStyleAttributeName: para,
                    }
                    ip = self._icon_paths[i] if i < len(self._icon_paths) else ""
                    icon_sz = 38.0 if is_sel else 34.0
                    if ip:
                        _ns_draw_app_icon_at(ip, tx, ty, icon_sz)
                        _ns_draw_string_in_rect(text, NSMakeRect(tx - 72.0, ty + 2.0, 144.0, 28.0), attrs)
                    else:
                        _ns_draw_string_in_rect(text, NSMakeRect(tx - 48.0, ty - 12.0, 96.0, 24.0), attrs)

                NSColor.colorWithCalibratedWhite_alpha_(1.0, 90.0 / 255.0).setStroke()
                for k in range(8):
                    ang_deg = float(k * 45.0 + 22.5)
                    ang = math.radians(ang_deg)
                    x2 = cx + math.cos(ang) * radius
                    y2 = cy - math.sin(ang) * radius
                    line = NSBezierPath.bezierPath()
                    line.moveToPoint_((cx, cy))
                    line.lineToPoint_((x2, y2))
                    line.setLineWidth_(1.0)
                    line.stroke()

                inner = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - inner_r, cy - inner_r, inner_r * 2.0, inner_r * 2.0))
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.0, 0.0, 120.0 / 255.0).setFill()
                inner.fill()
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 140.0 / 255.0).setStroke()
                inner.setLineWidth_(2.0)
                inner.stroke()

                def _draw_centered_text(y: float, height: float, s: str, *, bold: bool, size: float, color: Any) -> None:
                    font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
                    para = NSMutableParagraphStyle.alloc().init()
                    try:
                        para.setAlignment_(1)
                    except Exception:
                        pass
                    attrs = {
                        NSFontAttributeName: font,
                        NSForegroundColorAttributeName: color,
                        NSParagraphStyleAttributeName: para,
                    }
                    _ns_draw_string_in_rect(str(s), NSMakeRect(cx - inner_r, y, inner_r * 2.0, height), attrs)

                _draw_centered_text(cy - 18.0, 22.0, self._preset_title, bold=True, size=12.0, color=NSColor.colorWithCalibratedWhite_alpha_(1.0, 235.0 / 255.0))
                _draw_centered_text(cy + 2.0, 36.0, self._subtitle, bold=False, size=10.0, color=NSColor.colorWithCalibratedWhite_alpha_(1.0, 180.0 / 255.0))
                if self._action_msg:
                    _draw_centered_text(
                        cy + 38.0,
                        24.0,
                        self._action_msg,
                        bold=True,
                        size=11.0,
                        color=NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 200.0 / 255.0, 120.0 / 255.0, 230.0 / 255.0),
                    )
                if self._show_click:
                    _draw_centered_text(
                        cy + 62.0,
                        20.0,
                        "CLICK",
                        bold=True,
                        size=11.0,
                        color=NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 140.0 / 255.0, 1.0, 230.0 / 255.0),
                    )

    except Exception:
        PalmPieNativeView = None


def _get_or_create_panel() -> Any | None:
    """非アクティブNSPanel（最小）を1つ作って使い回す。"""

    global _PANEL_SINGLETON, _PANEL_LABEL, _PIE_VIEW
    if sys.platform != "darwin":
        return None
    if _PANEL_SINGLETON is not None:
        return _PANEL_SINGLETON
    try:
        from AppKit import (  # type: ignore[import-not-found]
            NSPanel,
            NSColor,
            NSBackingStoreBuffered,
            NSWindowStyleMaskBorderless,
            NSWindowStyleMaskNonactivatingPanel,
            NSWindowStyleMaskUtilityWindow,
        )
        from Foundation import NSMakeRect  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        rect = NSMakeRect(100.0, 100.0, 520.0, 520.0)
        style = (
            int(NSWindowStyleMaskBorderless)
            | int(NSWindowStyleMaskNonactivatingPanel)
            | int(NSWindowStyleMaskUtilityWindow)
        )
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(rect, style, NSBackingStoreBuffered, False)
        panel.setOpaque_(False)
        panel.setHasShadow_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)
        try:
            panel.setFloatingPanel_(True)
        except Exception:
            pass
        try:
            panel.setHidesOnDeactivate_(False)
        except Exception:
            pass
        try:
            panel.setMovable_(False)
        except Exception:
            pass
        if PalmPanelPassThroughContentView is not None:
            try:
                cv0 = PalmPanelPassThroughContentView.alloc().initWithFrame_(
                    NSMakeRect(0.0, 0.0, float(rect.size.width), float(rect.size.height))
                )
                panel.setContentView_(cv0)
            except Exception:
                pass

        cv = panel.contentView()
        bf = cv.bounds()
        if PalmPieNativeView is not None:
            try:
                from AppKit import NSViewHeightSizable, NSViewWidthSizable  # type: ignore[import-not-found]

                pie = PalmPieNativeView.alloc().initWithFrame_(bf)
                pie.setAutoresizingMask_(int(NSViewWidthSizable) | int(NSViewHeightSizable))
                cv.addSubview_(pie)
                _PIE_VIEW = pie
            except Exception:
                _PIE_VIEW = None
        if _PIE_VIEW is None:
            from AppKit import NSFont, NSTextField  # type: ignore[import-not-found]

            label = NSTextField.alloc().initWithFrame_(NSMakeRect(8.0, 8.0, 504.0, 28.0))
            label.setStringValue_("PieMenu overlay active")
            label.setBezeled_(False)
            label.setDrawsBackground_(True)
            try:
                label.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.75))
            except Exception:
                label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setAlignment_(1)
            try:
                label.setTextColor_(NSColor.whiteColor())
            except Exception:
                pass
            try:
                label.setFont_(NSFont.boldSystemFontOfSize_(14.0))
            except Exception:
                pass
            cv.addSubview_(label)
            _PANEL_LABEL = label

        _PANEL_SINGLETON = panel
        return panel
    except Exception:
        return None


def _panel_screen_for_mouse() -> Any | None:
    try:
        from AppKit import NSEvent, NSScreen  # type: ignore[import-not-found]

        mouse = NSEvent.mouseLocation()
        target = NSScreen.mainScreen()
        try:
            for sc in NSScreen.screens() or []:
                sf = sc.frame()
                if (
                    float(sf.origin.x) <= float(mouse.x) < float(sf.origin.x) + float(sf.size.width)
                    and float(sf.origin.y) <= float(mouse.y) < float(sf.origin.y) + float(sf.size.height)
                ):
                    target = sc
                    break
        except Exception:
            pass
        return target
    except Exception:
        return None


def _apply_panel_space_hints(panel: Any) -> None:
    if sys.platform != "darwin":
        return
    try:
        from AppKit import (  # type: ignore[import-not-found]
            NSMainMenuWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorMoveToActiveSpace,
        )

        extra = (
            int(NSWindowCollectionBehaviorCanJoinAllSpaces)
            | int(NSWindowCollectionBehaviorFullScreenAuxiliary)
            | int(NSWindowCollectionBehaviorMoveToActiveSpace)
            | int(NSWindowCollectionBehaviorIgnoresCycle)
        )
        cur_b = int(panel.collectionBehavior())
        panel.setCollectionBehavior_(cur_b | extra)
        if _overlay_mode() == "aggressive":
            try:
                panel.setLevel_(int(NSMainMenuWindowLevel))
            except Exception:
                pass
        try:
            panel.orderFrontRegardless()
        except Exception:
            pass
    except Exception:
        pass


def show_panel_overlay(
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    text: str = "PieMenu overlay active",
    pie_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Qt が全画面上に出せない場合の NSPanel フォールバック。

    pie_state が渡されたときは円形 PieMenu を描画する。未指定のときは画面上部の細いバナーのみ。
    """

    panel = _get_or_create_panel()
    if panel is None:
        return
    try:
        from Foundation import NSMakeRect  # type: ignore[import-not-found]
    except Exception:
        return
    try:
        try:
            panel.setIgnoresMouseEvents_(True)
            panel.setAcceptsMouseMovedEvents_(False)
        except Exception:
            pass
        global _PIE_VIEW, _PANEL_LABEL
        target = _panel_screen_for_mouse()
        try:
            from AppKit import NSScreen  # type: ignore[import-not-found]

            sf = target.frame() if target is not None else NSScreen.mainScreen().frame()
        except Exception:
            sf = None

        if pie_state is not None and _PIE_VIEW is not None:
            try:
                _PIE_VIEW.applyPieState_(pie_state)
            except Exception:
                pass
            try:
                if _PANEL_LABEL is not None:
                    _PANEL_LABEL.setHidden_(True)
            except Exception:
                pass
            try:
                _PIE_VIEW.setHidden_(False)
            except Exception:
                pass
            side = float(max(320, min(int(w), int(h), 560)))
            if sf is not None:
                px = float(sf.origin.x) + max(0.0, (float(sf.size.width) - side) / 2.0)
                py = float(sf.origin.y) + max(0.0, (float(sf.size.height) - side) / 2.0)
                panel.setFrame_display_(NSMakeRect(px, py, side, side), True)
                _debug_print(f"native pie panel px={px:.0f} py={py:.0f} side={side:.0f} (qt ref x={x} y={y})")
            else:
                panel.setFrame_display_(NSMakeRect(float(x), float(y), side, side), True)
        else:
            if _PIE_VIEW is not None:
                try:
                    _PIE_VIEW.applyBanner_(str(text))
                    _PIE_VIEW.setHidden_(False)
                except Exception:
                    pass
            if _PANEL_LABEL is not None:
                try:
                    _PANEL_LABEL.setStringValue_(str(text))
                    _PANEL_LABEL.setHidden_(False)
                except Exception:
                    pass
            try:
                from AppKit import NSScreen  # type: ignore[import-not-found]

                mouse = None
                try:
                    from AppKit import NSEvent  # type: ignore[import-not-found]

                    mouse = NSEvent.mouseLocation()
                except Exception:
                    pass
                tgt = target
                if tgt is None:
                    tgt = NSScreen.mainScreen()
                vf = tgt.visibleFrame() if tgt is not None else NSScreen.mainScreen().visibleFrame()
                pw = float(max(320, min(int(w), 560)))
                ph = 44.0
                px = float(vf.origin.x) + max(0.0, (float(vf.size.width) - pw) / 2.0)
                py = float(vf.origin.y) + float(vf.size.height) - ph - 12.0
                panel.setFrame_display_(NSMakeRect(px, py, pw, ph), True)
                _debug_print(f"panel banner px={px:.0f} py={py:.0f} pw={pw:.0f} ph={ph:.0f} mouse={mouse!r}")
            except Exception:
                panel.setFrame_display_(NSMakeRect(float(x), float(y), float(w), float(h)), True)

        _apply_panel_space_hints(panel)
        try:
            panel.orderFrontRegardless()
        except Exception:
            pass
    except Exception:
        return


def hide_panel_overlay() -> None:
    panel = _get_or_create_panel()
    if panel is None:
        return
    try:
        panel.orderOut_(None)
    except Exception:
        return


def is_macos_overlay_available() -> bool:
    """PyObjC(AppKit/objc) が利用可能かを返す。"""

    if sys.platform != "darwin":
        return False
    try:
        import AppKit  # noqa: F401
        import objc  # noqa: F401

        return True
    except Exception:
        return False


def _debug_enabled() -> bool:
    # macos_overlay のログも別フラグで明示的にONにしたときだけ出す
    v = str(os.environ.get("PALMCONTROL_MACOS_OVERLAY_DEBUG", "")).strip().lower()
    return v in ("1", "true", "yes", "on", "debug")


def _debug_print(msg: str) -> None:
    if not _debug_enabled():
        return
    try:
        print(f"[PalmControl][macos_overlay] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _overlay_mode() -> str:
    """macOS オーバーレイの適用モード。

    - off: 何もしない（安全）
    - safe: collectionBehavior のみ（比較的安全）
    - aggressive: level / orderFrontRegardless まで適用（最前面重視、環境によっては不安定）
    """

    v = str(os.environ.get("PALMCONTROL_MACOS_OVERLAY", "safe")).strip().lower()
    if v in ("0", "false", "off", "none"):
        return "off"
    if v in ("aggressive", "force", "2"):
        return "aggressive"
    return "safe"


def get_nswindow_diagnostics(widget: Any) -> Optional[Dict[str, Any]]:
    """QWidget の背後NSWindowの状態を取得する（macOSのみ）。"""

    if sys.platform != "darwin":
        return None
    try:
        from ctypes import c_void_p

        from objc import objc_object
    except Exception:
        return None
    try:
        wid = int(widget.winId())
    except Exception:
        return None
    if wid == 0:
        return None
    try:
        ns_view = objc_object(c_void_p=c_void_p(wid))
        win = ns_view.window()
        if win is None:
            return None
        info: Dict[str, Any] = {}
        try:
            info["class"] = str(win.className())
        except Exception:
            info["class"] = None
        for k, fn in (
            ("level", lambda: int(win.level())),
            ("styleMask", lambda: int(win.styleMask())),
            ("collectionBehavior", lambda: int(win.collectionBehavior())),
            ("isVisible", lambda: bool(win.isVisible())),
        ):
            try:
                info[k] = fn()
            except Exception:
                info[k] = None
        try:
            info["occlusionState"] = int(win.occlusionState())
        except Exception:
            info["occlusionState"] = None
        return info
    except Exception:
        return None


def apply_fullscreen_auxiliary_collection_behavior(widget: Any) -> None:
    """QWidget の背後にある NSWindow に、フルスクリーン空間へ追従する振る舞いを付与する。"""

    if sys.platform != "darwin":
        return
    mode = _overlay_mode()
    if mode == "off":
        return
    try:
        from ctypes import c_void_p

        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorMoveToActiveSpace,
        )
        try:
            # Quartz 由来のウィンドウレベル（CGWindowLevelForKey）を使える場合は優先する
            from Quartz import CGWindowLevelForKey, kCGAssistiveTechHighWindowLevelKey, kCGPopUpMenuWindowLevelKey, kCGScreenSaverWindowLevelKey

            _quartz = {
                "assistive": (CGWindowLevelForKey, kCGAssistiveTechHighWindowLevelKey),
                "popup": (CGWindowLevelForKey, kCGPopUpMenuWindowLevelKey),
                "screensaver": (CGWindowLevelForKey, kCGScreenSaverWindowLevelKey),
            }
        except Exception:
            _quartz = {}
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
        # 非アクティブ化（他アプリにフォーカス）されても隠れないようにする
        try:
            win.setHidesOnDeactivate_(False)
        except Exception:
            pass
        extra = (
            int(NSWindowCollectionBehaviorCanJoinAllSpaces)
            | int(NSWindowCollectionBehaviorFullScreenAuxiliary)
            # 表示したタイミングでアクティブスペースへ移動（YouTube全画面=別スペース対策）
            | int(NSWindowCollectionBehaviorMoveToActiveSpace)
        )
        # Alt-Tab/ウィンドウサイクルに出さない（オーバーレイ用途）
        extra = int(extra) | int(NSWindowCollectionBehaviorIgnoresCycle)
        cur = int(win.collectionBehavior())
        win.setCollectionBehavior_(cur | extra)
        if mode == "aggressive":
            # ネイティブフルスクリーン上で潜るケース向け。環境によっては不安定になり得る。
            # Qt の通常ウィンドウだと全画面スペース上で表示が抑制されることがあるため、
            # 可能なら「非アクティブパネル」に寄せる（f.lux 等の補助パネルに近い挙動）。
            try:
                from AppKit import (
                    NSWindowStyleMaskNonactivatingPanel,
                    NSWindowStyleMaskUtilityWindow,
                )

                cur_mask = int(win.styleMask())
                add_mask = int(NSWindowStyleMaskNonactivatingPanel) | int(NSWindowStyleMaskUtilityWindow)
                win.setStyleMask_(cur_mask | add_mask)
                try:
                    win.setFloatingPanel_(True)
                except Exception:
                    pass
            except Exception:
                pass
            try:
                # Quartz / AppKit のウィンドウレベル（環境変数 PALMCONTROL_MACOS_OVERLAY_LEVEL）
                # - assistive / popup / screensaver: CGWindowLevelForKey
                # - mainmenu: NSMainMenuWindowLevel（全画面上に出しやすい報告が多い）
                key = str(os.environ.get("PALMCONTROL_MACOS_OVERLAY_LEVEL", "assistive")).strip().lower()
                if key in _quartz:
                    fn, level_key = _quartz[key]
                    win.setLevel_(int(fn(level_key)))
                elif key in ("mainmenu", "menu", "main_menu"):
                    from AppKit import NSMainMenuWindowLevel

                    win.setLevel_(int(NSMainMenuWindowLevel))
                else:
                    from AppKit import NSScreenSaverWindowLevel, NSStatusWindowLevel

                    lvl = max(int(NSStatusWindowLevel), int(NSScreenSaverWindowLevel))
                    win.setLevel_(int(lvl))
            except Exception:
                pass
            try:
                # フォーカスが別アプリでも前面へ
                win.orderFrontRegardless()
            except Exception:
                pass

        d = get_nswindow_diagnostics(widget)
        if d is not None:
            _debug_print(
                "nswindow "
                + " ".join(
                    f"{k}={d.get(k)}" for k in ("class", "level", "styleMask", "collectionBehavior", "isVisible", "occlusionState")
                )
            )
    except Exception:
        # 将来の Qt / macOS 差分では失敗し得るため、表示不能より無視して継続する。
        return


def apply_macos_overlay_hints(widget: Any) -> None:
    """PieMenu のようなオーバーレイ向けの macOS ヒントをまとめて適用する。"""

    apply_fullscreen_auxiliary_collection_behavior(widget)
