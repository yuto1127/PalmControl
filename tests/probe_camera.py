from __future__ import annotations

import platform
from pathlib import Path

import cv2


def main() -> int:
    print("[PalmControl] Camera probe start")
    print(f"platform={platform.system()}")

    # Windowsではバックエンド差を見たいので複数試す。
    if platform.system().lower() == "windows":
        backends = [
            ("dshow", cv2.CAP_DSHOW),
            ("msmf", cv2.CAP_MSMF),
            ("default", None),
        ]
    else:
        backends = [("default", None)]

    found = False
    for device_id in range(6):
        opened_names: list[str] = []
        for name, backend in backends:
            if backend is None:
                cap = cv2.VideoCapture(device_id)
            else:
                cap = cv2.VideoCapture(device_id, backend)
            ok = bool(cap is not None and cap.isOpened())
            try:
                cap.release()
            except Exception:
                pass
            if ok:
                opened_names.append(name)
        if opened_names:
            found = True
            print(f"device_id={device_id} opened_backends={','.join(opened_names)}")

    if not found:
        print("camera_probe=NG")
        print("hint=Windows privacy settings or camera in-use by another app")
        return 1

    print("camera_probe=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
