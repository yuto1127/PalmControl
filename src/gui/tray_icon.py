from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


class TrayIcon(QSystemTrayIcon):
    """トレイ常駐（メニューバー/タスクトレイ）管理。"""

    showRequested = pyqtSignal()
    pauseRequested = pyqtSignal()
    exitRequested = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        # アイコンは最小実装として標準テーマに委ねる（後で差し替え可）
        super().__init__(QIcon(), parent)
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

