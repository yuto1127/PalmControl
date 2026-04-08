from __future__ import annotations

import math
import time
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Optional, Tuple

from src.utils.config_loader import ConfigStore, Settings, get_config_store
from src.utils.logger import JsonPrependLogger

# MediaPipe TasksがGPUサービス（OpenGL）を要求する環境があり、
# ヘッドレス/権限制限下では初期化に失敗することがある。
# 研究用ツールとして環境差を減らすため、基本はCPUで動かす方針とする。
os.environ.setdefault("MEDIAPIPE_DISABLE_GPU", "1")

import mediapipe as mp


DEFAULT_HAND_MODEL_PATH = Path("models/hand_landmarker.task")


@dataclass(frozen=True)
class DetectionResult:
    """検出結果（制御は行わず、解析結果だけを返す）。

    研究用途の意図:
    - Controller層と切り離し、同一の入力フレームに対して常に同じ解析結果が得られる形にする。
    - 後でログ/可視化/リプレイ検証ができるよう、最低限のメタ情報も含める。
    """

    mode: str  # "Mouse" | "Scroll" | "None"
    finger_count: int
    pointer_xy: Optional[Tuple[float, float]]  # 正規化座標(0..1)。Mouseモード時にのみ有効
    contact: bool  # 親指と人差し指が接触（しきい値以下）しているか
    handedness: Optional[str]  # "Left" | "Right" | None
    latency_ms: float
    # テスト/可視化用に、MediaPipeのランドマークをそのまま返す（無ければNone）
    hand_landmarks: Any | None


class HandDetector:
    """MediaPipe Handsで手の状態を解析するクラス。

    実装ポイント（spec準拠）:
    - MediaPipe Tasksの HandLandmarker をVIDEOモードで使用（追跡によりリアルタイム性を確保）
    - 設定は `config/settings.yaml` を参照（confidence, frame_skip, click_threshold, ROI）
    - 指本数優先でモードを排他的に決める（5本=Scroll, 2本=Mouse）
    - Mouseモードでは、(index_tip, middle_tip) の中点を正規化座標で返す
    - 親指と人差し指の距離でコンタクト判定を返す
    - 処理時間を測定し、ロガーがあれば出力できるようにする
    """

    def __init__(
        self,
        store: Optional[ConfigStore] = None,
        *,
        logger: Optional[JsonPrependLogger] = None,
        mirror_x: bool = True,
        max_num_hands: int = 2,
        model_path: str | Path = DEFAULT_HAND_MODEL_PATH,
    ) -> None:
        self._store = store or get_config_store("config/settings.yaml")
        self._logger = logger
        self._mirror_x = bool(mirror_x)
        self._max_num_hands = int(max_num_hands)
        self._model_path = Path(model_path)

        self._frame_index = 0
        self._t0 = time.perf_counter()
        self._landmarker = self._build_landmarker(self._store.get())

        # detection.* は重要変更扱いではないので通知されない可能性がある。
        # そのため、settingsのスナップショットを毎フレーム参照し、必要な値は都度読む。

    def close(self) -> None:
        """MediaPipeリソースを解放する。"""

        try:
            self._landmarker.close()
        except Exception:
            pass

    def process(self, frame_bgr) -> DetectionResult:
        """BGRフレームを解析し、ジェスチャー状態を返す。"""

        t0 = time.perf_counter()
        settings = self._store.get()

        # フレーム間引き（負荷抑制）
        frame_skip = int(settings.detection.frame_skip)
        self._frame_index += 1
        if frame_skip > 0 and (self._frame_index % (frame_skip + 1)) != 1:
            dt_ms = (time.perf_counter() - t0) * 1000.0
            return DetectionResult(
                mode="None",
                finger_count=0,
                pointer_xy=None,
                contact=False,
                handedness=None,
                latency_ms=dt_ms,
                hand_landmarks=None,
            )

        # MediaPipe Tasksは mp.Image（SRGB）を受け取る
        frame_rgb = frame_bgr[:, :, ::-1]
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        timestamp_ms = int((time.perf_counter() - self._t0) * 1000.0)

        mp_result = self._landmarker.detect_for_video(mp_image, timestamp_ms)
        result = self._analyze_mediapipe_result(mp_result, settings)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        result = DetectionResult(
            mode=result.mode,
            finger_count=result.finger_count,
            pointer_xy=result.pointer_xy,
            contact=result.contact,
            handedness=result.handedness,
            latency_ms=dt_ms,
            hand_landmarks=result.hand_landmarks,
        )

        if self._logger is not None:
            self._logger.write(
                "INFO",
                "vision.detector",
                {
                    "latency_ms": result.latency_ms,
                    "mode": result.mode,
                    "finger_count": result.finger_count,
                    "contact": result.contact,
                },
            )
        return result

    def _build_landmarker(self, settings: Settings):
        """HandLandmarker(Task API)を構築する。"""

        if not self._model_path.exists():
            raise FileNotFoundError(
                "HandLandmarkerのモデルファイルが見つかりません。\n"
                f"- 期待パス: {self._model_path}\n"
                "次のURLから `hand_landmarker.task` をダウンロードして `models/` に配置してください。\n"
                "- URL: https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task\n"
            )

        BaseOptions = mp.tasks.BaseOptions
        HandLandmarker = mp.tasks.vision.HandLandmarker
        HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        options = HandLandmarkerOptions(
            base_options=BaseOptions(
                model_asset_path=str(self._model_path),
                # GUIやOpenGLが使えない環境でも動くよう、CPUを明示してGPU依存を避ける。
                delegate=BaseOptions.Delegate.CPU,
            ),
            running_mode=VisionRunningMode.VIDEO,
            num_hands=self._max_num_hands,
            min_hand_detection_confidence=float(settings.detection.min_detection_confidence),
            min_tracking_confidence=float(settings.detection.min_tracking_confidence),
        )
        return HandLandmarker.create_from_options(options)

    def _analyze_mediapipe_result(self, mp_result, settings: Settings) -> DetectionResult:
        """MediaPipeの出力をPalmControlの研究用表現へ落とし込む。"""

        if not getattr(mp_result, "hand_landmarks", None):
            return DetectionResult(
                mode="None",
                finger_count=0,
                pointer_xy=None,
                contact=False,
                handedness=None,
                latency_ms=0.0,
                hand_landmarks=None,
            )

        # 初期段階では「一番最初の手」だけを採用する。
        # 両手が映った場合の優先順位（利き手、画面中央に近い方など）は後で研究しやすいように切り出し可能。
        hand_landmarks = mp_result.hand_landmarks[0]

        handedness: Optional[str] = None
        if getattr(mp_result, "handedness", None):
            try:
                handedness = mp_result.handedness[0][0].category_name
            except Exception:
                handedness = None

        # 指の伸展・屈曲状態（Finger Count Priority）
        finger_states = self._get_finger_states(hand_landmarks, handedness)
        finger_count = sum(1 for v in finger_states.values() if v)

        # 排他的モード判定
        if finger_count == 5:
            mode = "Scroll"
        elif self._is_mouse_mode(finger_states):
            mode = "Mouse"
        else:
            mode = "None"

        # コン タクト判定（親指 tip=4 と 人差し指 tip=8 の距離）
        contact = self._is_contact(hand_landmarks, click_threshold=float(settings.control.click_threshold))

        pointer_xy: Optional[Tuple[float, float]] = None
        if mode == "Mouse":
            pointer_xy = self._compute_pointer_xy(hand_landmarks, settings)

        return DetectionResult(
            mode=mode,
            finger_count=finger_count,
            pointer_xy=pointer_xy,
            contact=contact,
            handedness=handedness,
            latency_ms=0.0,
            hand_landmarks=hand_landmarks,
        )

    @staticmethod
    def _is_mouse_mode(finger_states: dict) -> bool:
        """2本指（人差し指・中指）だけ伸展している状態かを判定する。"""

        index_ext = bool(finger_states.get("index", False))
        middle_ext = bool(finger_states.get("middle", False))
        ring_ext = bool(finger_states.get("ring", False))
        pinky_ext = bool(finger_states.get("pinky", False))
        return index_ext and middle_ext and (not ring_ext) and (not pinky_ext)

    @staticmethod
    def _get_finger_states(hand_landmarks, handedness: Optional[str]) -> dict:
        """指の伸展状態を推定する。

        基本方針:
        - 人差し指〜小指: tip.y < pip.y なら伸展（画像座標は上が小さい）
        - 親指: 左右で向きが逆転するため、handednessに応じてx方向で判定
        """

        lm = hand_landmarks

        def ext_by_y(tip: int, pip: int) -> bool:
            return lm[tip].y < lm[pip].y

        # 親指はIP(3)とTIP(4)のxを使う（Right/Leftで条件反転）
        if handedness == "Right":
            thumb_ext = lm[4].x < lm[3].x
        elif handedness == "Left":
            thumb_ext = lm[4].x > lm[3].x
        else:
            # handedness不明なら、単純なx比較で暫定
            thumb_ext = lm[4].x < lm[3].x

        return {
            "thumb": bool(thumb_ext),
            "index": ext_by_y(8, 6),
            "middle": ext_by_y(12, 10),
            "ring": ext_by_y(16, 14),
            "pinky": ext_by_y(20, 18),
        }

    @staticmethod
    def _is_contact(hand_landmarks, *, click_threshold: float) -> bool:
        """親指と人差し指の距離がしきい値以下かを返す。"""

        lm = hand_landmarks
        dx = lm[4].x - lm[8].x
        dy = lm[4].y - lm[8].y
        dist = math.sqrt(dx * dx + dy * dy)
        return dist <= float(click_threshold)

    def _compute_pointer_xy(self, hand_landmarks, settings: Settings) -> Tuple[float, float]:
        """Mouseモード用のポインタ座標（正規化0..1）を返す。

        仕様:
        - 人差し指(8)と中指(12)の先端の中間点を採用
        - ROIが有効ならROI内で正規化し直し、少ない手の動きで全画面操作しやすくする
        - 返り値のXは「ユーザー視点で直感的」になるよう鏡補正（mirror_x=Trueなら反転）
        """

        lm = hand_landmarks
        x = (lm[8].x + lm[12].x) / 2.0
        y = (lm[8].y + lm[12].y) / 2.0

        roi = settings.camera.roi
        if roi.enabled:
            # ROI内に収めたうえで (roi基準) 0..1 に再正規化する
            x = (x - roi.x) / max(roi.w, 1e-9)
            y = (y - roi.y) / max(roi.h, 1e-9)
            x = min(max(x, 0.0), 1.0)
            y = min(max(y, 0.0), 1.0)

        if self._mirror_x:
            x = 1.0 - x
        return (float(x), float(y))

