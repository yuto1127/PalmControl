from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from src.gui.main_window import MainWindow
from src.gui.tray_icon import TrayIcon
from src.gui.worker import VisionControlWorker
from src.utils.config_loader import ConfigStore


def main() -> int:
    app = QApplication(sys.argv)

    store = ConfigStore()
    worker = VisionControlWorker(store)
    worker.start()

    win = MainWindow(store, worker)
    tray = TrayIcon()

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
    return code


if __name__ == "__main__":
    raise SystemExit(main())

