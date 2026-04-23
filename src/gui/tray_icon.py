from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon


class TrayIcon(QSystemTrayIcon):
    """トレイ常駐（メニューバー/タスクトレイ）管理。"""

    showRequested = pyqtSignal()
    pauseRequested = pyqtSignal()
    exitRequested = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        # Windowsでは空アイコンだと表示されないため、標準アイコンを必ず設定する。
        fallback_icon = QIcon()
        app = QApplication.instance()
        if app is not None:
            fallback_icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        super().__init__(fallback_icon, parent)
        self.setToolTip("PalmControl")

        menu = QMenu()

        act_show = QAction("設定を開く")
        act_show.triggered.connect(self.showRequested.emit)
        menu.addAction(act_show)

        act_pause = QAction("一時停止")
        act_pause.triggered.connect(self.pauseRequested.emit)
        menu.addAction(act_pause)

        menu.addSeparator()

        act_exit = QAction("終了")
        act_exit.triggered.connect(self.exitRequested.emit)
        menu.addAction(act_exit)

        self.setContextMenu(menu)

