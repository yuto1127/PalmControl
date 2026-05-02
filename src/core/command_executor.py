from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional

import pyautogui


@dataclass(frozen=True)
class CommandSpec:
    """PieMenuスロットから実行されるコマンド定義。

    type:
    - shortcut: PyAutoGUI風の "ctrl+shift+p" 等
    - application: アプリ/ファイルへのパス
    """

    label: str
    type: str  # "shortcut" | "application"
    value: str


class CommandExecutor:
    """ショートカット/アプリ起動を実行する薄い実行層。

    重要な意図:
    - GUIやVisionスレッドから直接OS操作APIを触らず、この層に寄せて疎結合にする。
    - 失敗してもアプリ本体を落とさない（研究用途の作業継続を優先）。
    """

    def execute(self, spec: CommandSpec) -> bool:
        typ = str(spec.type).strip().lower()
        if typ == "application":
            return self._open_application(str(spec.value))
        # default: shortcut
        return self._send_shortcut(str(spec.value))

    @staticmethod
    def _send_shortcut(expr: str) -> bool:
        """PyAutoGUI風の 'ctrl+shift+p' を送出する。"""

        expr = (expr or "").strip()
        if not expr:
            return False

        # 例: "ctrl+shift+p" / "cmd+space" / "media_play_pause"
        keys = [k.strip().lower() for k in expr.split("+") if k.strip()]
        if not keys:
            return False

        # GUI やユーザー入力で「cmd」「option」等が混在しても PyAutoGUI の正式名へ寄せる
        def _normalize_key_token(tok: str) -> str:
            t = str(tok).strip().lower()
            if sys.platform.startswith("darwin"):
                if t == "cmd":
                    return "command"
                if t in ("option", "opt"):
                    return "option"
            return t

        keys = [_normalize_key_token(k) for k in keys]

        try:
            # macOS: 音量系はキー送出より「直接変更」の方が安定する（フォーカス不要）
            if sys.platform.startswith("darwin") and len(keys) == 1:
                k = keys[0]
                if k in ("volumeup", "volumedown", "volumemute"):
                    return CommandExecutor._mac_volume(k)

            # Windows: Core Audio（既定再生デバイス）を直接操作。pycaw 未導入時は後段の pyautogui にフォールバック。
            if os.name == "nt" and len(keys) == 1:
                k = keys[0]
                if k in ("volumeup", "volumedown", "volumemute"):
                    if CommandExecutor._win_volume(k):
                        return True

            if len(keys) == 1:
                pyautogui.press(keys[0])
            else:
                pyautogui.hotkey(*keys)
            return True
        except Exception:
            return False

    @staticmethod
    def _mac_volume(kind: str) -> bool:
        """macOSのシステム音量をAppleScriptで変更する。"""

        kind = str(kind).strip().lower()
        try:
            if kind == "volumeup":
                # 0..100 の範囲で+6
                script = (
                    "set cur to output volume of (get volume settings)\n"
                    "set nxt to cur + 6\n"
                    "if nxt > 100 then set nxt to 100\n"
                    "set volume output volume nxt\n"
                    "set volume output muted false\n"
                )
            elif kind == "volumedown":
                script = (
                    "set cur to output volume of (get volume settings)\n"
                    "set nxt to cur - 6\n"
                    "if nxt < 0 then set nxt to 0\n"
                    "set volume output volume nxt\n"
                )
            else:
                # volumemute: toggle
                script = (
                    "set m to output muted of (get volume settings)\n"
                    "set volume output muted (not m)\n"
                )
            subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)
            return True
        except Exception:
            return False

    @staticmethod
    def _win_volume(kind: str) -> bool:
        """Windows の既定出力デバイス音量を Core Audio（IAudioEndpointVolume）で変更する。

        macOS の osascript と同様、フォーカスやキーボードレイアウトに依存しない。
        ステップは mac の ±6（0..100）に近づけるため、スカラーで約 ±0.06 とする。
        """

        kind = str(kind).strip().lower()
        try:
            from pycaw.pycaw import AudioUtilities
        except Exception:
            return False
        try:
            speakers = AudioUtilities.GetSpeakers()
            if speakers is None:
                return False
            vol = speakers.EndpointVolume
            # 0.0..1.0（スカラー）。mac の 6% 刻みに概ね合わせる
            step = 0.06
            if kind == "volumeup":
                cur = float(vol.GetMasterVolumeLevelScalar())
                vol.SetMasterVolumeLevelScalar(min(1.0, cur + step), None)
                vol.SetMute(False, None)
                return True
            if kind == "volumedown":
                cur = float(vol.GetMasterVolumeLevelScalar())
                vol.SetMasterVolumeLevelScalar(max(0.0, cur - step), None)
                return True
            if kind == "volumemute":
                muted = bool(vol.GetMute())
                vol.SetMute(not muted, None)
                return True
        except Exception:
            return False
        return False

    @staticmethod
    def _open_application(path: str) -> bool:
        path = (path or "").strip()
        if not path:
            return False

        try:
            if sys.platform.startswith("darwin"):
                # macOS: .app もファイルも open に委ねる
                subprocess.Popen(["open", path])
                return True
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
                return True
            # linux/other
            subprocess.Popen(["xdg-open", path])
            return True
        except Exception:
            return False

