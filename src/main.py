from __future__ import annotations

import os
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.command_executor import CommandExecutor, CommandSpec
from src.core.media_preset import media_actions_by_id
from src.gui.main_window import MainWindow
from src.gui.modern_style import apply_modern_theme
from src.gui.pie_menu import PieMenuOverlay
from src.gui.tray_icon import TrayIcon
from src.gui.worker import VisionControlWorker
from src.utils.config_loader import ConfigStore


def main() -> int:
    # Windows: ターミナル出力の日本語が文字化けしやすい（コードページ差）
    # PowerShell / Windows Terminal 側が UTF-8 のときに合わせ、stdout/stderr を UTF-8 に寄せる。
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    app = QApplication(sys.argv)
    apply_modern_theme(app)

    try:
        store = ConfigStore()
    except Exception as e:
        QMessageBox.critical(
            None,
            "PalmControl — 設定を読み込めません",
            f"config/settings.yaml を確認してください。\n\n{e}",
        )
        return 1
    worker = VisionControlWorker(store)

    win = MainWindow(store, worker)
    tray = TrayIcon()
    pie = PieMenuOverlay(store)
    executor = CommandExecutor()

    def _spec_for(preset: int, slot: int) -> CommandSpec:
        s = store.get()
        preset = int(preset)
        slot = int(slot)

        if preset == 2:
            ids = list(getattr(s.pie_menu, "preset2_layout"))
            act_id = str(ids[slot - 1])
            act = media_actions_by_id()[act_id]
            return CommandSpec(label=act.label, type=act.type, value=act.value)

        if preset == 3:
            sl = s.pie_menu.custom_3.slots[slot - 1]
        else:
            sl = s.pie_menu.custom_1.slots[slot - 1]
        return CommandSpec(label=str(sl.label), type=str(sl.type), value=str(sl.value))

    def _on_slot_triggered(preset: int, slot: int) -> None:
        spec = _spec_for(preset, slot)
        ok = executor.execute(spec)
        try:
            lab = (spec.label or "").strip()
            name = lab if lab else f"Preset{preset}-Slot{slot}"
            pie.set_action_feedback(f"{name}: {'OK' if ok else 'NG'}")
        except Exception:
            pass

    pie.slotTriggered.connect(_on_slot_triggered)

    def _on_pie_state(state: dict) -> None:
        try:
            active = bool(state.get("active"))
            ptr = state.get("pointer") or {}
            cmd = state.get("command") or {}
            pointer_xy = ptr.get("pointer_xy")
            # pointer_xy は (x,y) タプルの想定だが、安全側で型チェック
            if not (
                isinstance(pointer_xy, (tuple, list))
                and len(pointer_xy) == 2
                and all(isinstance(v, (int, float)) for v in pointer_xy)
            ):
                pointer_xy = None

            pie.set_active(active, pointer_xy=pointer_xy)
            pie.update_pointer(pointer_xy)

            # 非利き手ジェスチャーに応じてプリセットを固定表示（中央クリックでの循環も可能）
            if active:
                p = int(cmd.get("preset") or 0)
                if p in (1, 2, 3):
                    pie.set_preset(p)

            # プリセット切替は「中央クリック」へ移行したため、scroll由来の切替は行わない

            # クリックイベント（実行後も閉じない）
            if bool(ptr.get("left_clicked")):
                pie.handle_click(right=False)
            if bool(ptr.get("right_clicked")):
                pie.handle_click(right=True)
        except Exception:
            # GUI連携の失敗で本体を落とさない
            pass

    worker.pieMenuStateReady.connect(_on_pie_state)

    # MainWindow 内で frameReady/statusReady/error を接続した後に開始する。
    # 先に start すると未取得シグナルが取りこぼされたり、環境によってはエラー通知が安定しない。
    worker.start()

    def show_window() -> None:
        win.show()
        win.raise_()
        win.activateWindow()

    def toggle_pause() -> None:
        # 最小: Control(=OS操作)だけを落とす。プレビューはタブ依存でON/OFF。
        current = win._control_cb.isChecked()  # type: ignore[attr-defined]
        win.set_control_enabled(not current)

    def exit_app() -> None:
        try:
            worker.stop()
            worker.wait(1500)
        except Exception:
            pass
        try:
            pie.close()
        except Exception:
            pass
        app.quit()

    tray.showRequested.connect(show_window)
    tray.pauseRequested.connect(toggle_pause)
    tray.exitRequested.connect(exit_app)
    win.exitRequested.connect(exit_app)
    tray.activated.connect(lambda _: show_window())
    tray.show()

    # 画面を閉じたら終了ではなく「隠す」にする（常駐）
    def on_close_event(event) -> None:
        win.hide()
        event.ignore()

    win.closeEvent = on_close_event  # type: ignore[assignment]
    win.show()

    code = app.exec()
    try:
        worker.stop()
        worker.wait(1500)
    except Exception:
        pass
    try:
        pie.close()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())

