from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2


@dataclass(frozen=True)
class CameraOpenResult:
    cap: Optional[cv2.VideoCapture]
    backend_name: str
    tried_backends: Tuple[str, ...]


def open_camera(device_id: int) -> CameraOpenResult:
    """OpenCVカメラを環境差を吸収しながら開く。

    Windowsではデバイスやドライバ相性で既定バックエンドが失敗することがあるため、
    DirectShow / Media Foundation / default の順で試す。
    """

    tried: List[str] = []
    system = platform.system().lower()

    candidates: List[Tuple[str, Optional[int]]]
    if system == "windows":
        candidates = [
            ("dshow", cv2.CAP_DSHOW),
            ("msmf", cv2.CAP_MSMF),
            ("default", None),
        ]
    else:
        candidates = [("default", None)]

    for name, backend in candidates:
        tried.append(name)
        if backend is None:
            cap = cv2.VideoCapture(int(device_id))
        else:
            cap = cv2.VideoCapture(int(device_id), int(backend))

        if cap is not None and cap.isOpened():
            return CameraOpenResult(cap=cap, backend_name=name, tried_backends=tuple(tried))

        try:
            cap.release()
        except Exception:
            pass

    return CameraOpenResult(cap=None, backend_name="none", tried_backends=tuple(tried))
