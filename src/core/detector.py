from __future__ import annotations

import math
import time
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.error import URLError
from urllib.request import urlretrieve

from src.utils.config_loader import ConfigStore, Settings, get_config_store
from src.utils.logger import JsonPrependLogger

# MediaPipe TasksがGPUサービス（OpenGL）を要求する環境があり、
# ヘッドレス/権限制限下では初期化に失敗することがある。
# 研究用ツールとして環境差を減らすため、基本はCPUで動かす方針とする。
os.environ.setdefault("MEDIAPIPE_DISABLE_GPU", "1")

import mediapipe as mp


DEFAULT_HAND_MODEL_PATH = Path("models/hand_landmarker.task")
DEFAULT_HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)


@dataclass(frozen=True)
class DetectionResult:
    """検出結果（制御は行わず、解析結果だけを返す）。

    研究用途の意図:
    - Controller層と切り離し、同一の入力フレームに対して常に同じ解析結果が得られる形にする。
    - 後でログ/可視化/リプレイ検証ができるよう、最低限のメタ情報も含める。
    """

    mode: str  # "Mouse" | "Scroll" | "None"
    finger_count: int
    thumb_extended: bool
    index_extended: bool
    middle_extended: bool
    pointer_xy: Optional[Tuple[float, float]]  # 正規化座標(0..1)。Mouseモード時にのみ有効
    contact: bool  # 親指と人差し指が接触（しきい値以下）しているか
    contact_distance: Optional[float]  # 親指-人差し指距離（正規化）。デバッグ用
    handedness: Optional[str]  # "Left" | "Right" | None
    latency_ms: float
    # テスト/可視化用に、MediaPipeのランドマークをそのまま返す（無ければNone）
    hand_landmarks: Any | None


@dataclass(frozen=True)
class DualHandResult:
    """両手の検出結果。

    目的:
    - MediaPipeのmulti-hand出力を「Left/Right」で正規化して保持する。
    - 利き手/非利き手の役割割当（Pointer/Command）を上位層（Worker）で行えるようにする。
    """

    left: Optional[DetectionResult]
    right: Optional[DetectionResult]


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

        # contact判定を安定させるための状態
        self._contact_state = False
        self._contact_dist_ema: Optional[float] = None
        self._contact_below_count = 0
        self._contact_above_count = 0

        # detection.* は重要変更扱いではないので通知されない可能性がある。
        # そのため、settingsのスナップショットを毎フレーム参照し、必要な値は都度読む。

    def close(self) -> None:
        """MediaPipeリソースを解放する。"""

        try:
            self._landmarker.close()
        except Exception:
            pass

    def process(self, frame_bgr) -> DualHandResult:
        """BGRフレームを解析し、左右手の状態を返す。"""

        t0 = time.perf_counter()
        settings = self._store.get()

        # フレーム間引き（負荷抑制）
        frame_skip = int(settings.detection.frame_skip)
        self._frame_index += 1
        if frame_skip > 0 and (self._frame_index % (frame_skip + 1)) != 1:
            dt_ms = (time.perf_counter() - t0) * 1000.0
            empty = DetectionResult(
                mode="None",
                finger_count=0,
                thumb_extended=False,
                index_extended=False,
                middle_extended=False,
                pointer_xy=None,
                contact=False,
                contact_distance=None,
                handedness=None,
                latency_ms=dt_ms,
                hand_landmarks=None,
            )
            return DualHandResult(left=None, right=None)

        # MediaPipe Tasksは mp.Image（SRGB）を受け取る
        frame_rgb = frame_bgr[:, :, ::-1]
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        timestamp_ms = int((time.perf_counter() - self._t0) * 1000.0)

        mp_result = self._landmarker.detect_for_video(mp_image, timestamp_ms)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        dual = self._analyze_mediapipe_result_multi(mp_result, settings, dt_ms=dt_ms)

        if self._logger is not None:
            # 研究ログは「現状の代表値」を残せればよいので、Right優先→Leftの順で1件だけ出す。
            rep = dual.right or dual.left
            if rep is not None:
                self._logger.write(
                    "INFO",
                    "vision.detector",
                    {
                        "latency_ms": rep.latency_ms,
                        "mode": rep.mode,
                        "finger_count": rep.finger_count,
                        "contact": rep.contact,
                        "contact_distance": rep.contact_distance,
                        "handedness": rep.handedness,
                    },
                )
        return dual

    def _build_landmarker(self, settings: Settings):
        """HandLandmarker(Task API)を構築する。"""

        if not self._model_path.exists():
            self._download_model_if_missing()
        if not self._model_path.exists():
            raise FileNotFoundError(
                "HandLandmarkerのモデルファイルが見つかりません。\n"
                f"- 期待パス: {self._model_path}\n"
                "ネットワーク接続後に再起動するか、次のURLから手動配置してください。\n"
                f"- URL: {DEFAULT_HAND_MODEL_URL}\n"
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

    def _download_model_if_missing(self) -> None:
        """モデル未配置時に自動ダウンロードを試みる。"""
        try:
            self._model_path.parent.mkdir(parents=True, exist_ok=True)
            urlretrieve(DEFAULT_HAND_MODEL_URL, self._model_path)  # nosec: trusted public model asset
        except (URLError, OSError):
            # 失敗時は後段で明示エラーを出す。
            pass

    def _analyze_mediapipe_result_multi(self, mp_result, settings: Settings, *, dt_ms: float) -> DualHandResult:
        """MediaPipeのmulti-hand出力を左右手へ正規化する。"""

        if not getattr(mp_result, "hand_landmarks", None):
            self._reset_contact_state()
            return DualHandResult(left=None, right=None)

        left: Optional[DetectionResult] = None
        right: Optional[DetectionResult] = None

        n = len(mp_result.hand_landmarks)
        for i in range(n):
            hand_landmarks = mp_result.hand_landmarks[i]
            handedness: Optional[str] = None
            if getattr(mp_result, "handedness", None):
                try:
                    handedness = mp_result.handedness[i][0].category_name
                except Exception:
                    handedness = None

            # NOTE:
            # handedness は MediaPipe が推定する「人の左右」であり、表示上の鏡反転(mirror_x)とは独立に扱う。
            # ここでmirror_xに合わせて左右を反転させると、物理的な右手/左手の役割が入れ替わり誤動作しやすい。

            one = self._analyze_single_hand(hand_landmarks, handedness, settings, dt_ms=dt_ms)
            if handedness == "Left":
                left = one
            elif handedness == "Right":
                right = one
            else:
                # handednessが確定しない手は左右に割り当てない（役割混線防止）
                pass

        return DualHandResult(left=left, right=right)

    def _analyze_single_hand(
        self, hand_landmarks, handedness: Optional[str], settings: Settings, *, dt_ms: float
    ) -> DetectionResult:
        """単一手のランドマークをPalmControlの研究用表現へ落とし込む。"""

        # 指の伸展・屈曲状態（Finger Count Priority）
        finger_states = self._get_finger_states(hand_landmarks, handedness)
        finger_count = sum(1 for v in finger_states.values() if v)

        # 排他的モード判定
        # Scrollは「パー」相当だが、実運用では親指だけ判定がブレやすい。
        # 研究用途として操作感を優先し、index〜pinkyの4本が伸展していればScrollとする。
        if self._is_scroll_mode(finger_states):
            mode = "Scroll"
        elif self._is_mouse_mode(finger_states):
            mode = "Mouse"
        else:
            mode = "None"

        # contact_distはデバッグ上重要なので、可能な限り常に返す。
        # 以前はモード外でNoneにしていたが、これだと「なぜ判定されないか」の調整が難しい。
        raw_contact_dist = self._contact_distance(hand_landmarks)

        # コンタクト判定（つまみ / pinch）
        #
        # 仕様書(4.3.2)の基本は「2本指モード維持中」だが、現実のつまみ動作では
        # - 人差し指が曲がる（伸展判定がFalseになる）
        # - 中指が不安定
        # が起きやすい。
        #
        # そこで研究用途として、Scroll以外のときに次のいずれかを満たせばcontact判定を有効化する:
        # - 人差し指が伸展している
        # - 中指が伸展している
        # - すでに距離が「予兆しきい値」以下（つまみ姿勢に入っている）
        index_ext = bool(finger_states.get("index", False))
        middle_ext = bool(finger_states.get("middle", False))
        thumb_ext = bool(finger_states.get("thumb", False))
        pre_th = float(settings.control.cursor_anchoring.pre_contact_threshold)
        contact_enabled = (mode != "Scroll") and (index_ext or middle_ext or (raw_contact_dist <= pre_th))

        if contact_enabled:
            contact, contact_distance = self._stable_contact(
                hand_landmarks,
                click_threshold=float(settings.control.click_threshold),
                release_threshold=pre_th,
                # contactだけは応答性を優先して確定フレーム数を軽くする
                confirm_frames=1,
            )
        else:
            self._reset_contact_state()
            contact = False
            contact_distance = raw_contact_dist

        pointer_xy: Optional[Tuple[float, float]] = None
        if mode == "Mouse":
            pointer_xy = self._compute_pointer_xy(hand_landmarks, settings)

        return DetectionResult(
            mode=mode,
            finger_count=finger_count,
            thumb_extended=thumb_ext,
            index_extended=index_ext,
            middle_extended=middle_ext,
            pointer_xy=pointer_xy,
            contact=contact,
            contact_distance=contact_distance,
            handedness=handedness,
            latency_ms=float(dt_ms),
            hand_landmarks=hand_landmarks,
        )

    def _reset_contact_state(self) -> None:
        """contact判定の内部状態をリセットする。

        意図:
        - contactは「2本指モード中の親指コンタクト」という文脈依存の状態。
        - モード外に出たとき（Scroll/None/手なし）は状態を捨てた方が誤検出しにくい。
        """

        self._contact_state = False
        self._contact_below_count = 0
        self._contact_above_count = 0
        self._contact_dist_ema = None

    @staticmethod
    def _is_mouse_mode(finger_states: dict) -> bool:
        """Mouseモード（カーソル操作）の判定。

        初期仕様では「人差し指＋中指が伸展、薬指＋小指は屈曲」を厳密に要求していたが、
        実運用ではリング/ピンキーの伸展判定が揺れやすく、Mouseモードに入りにくい。

        Scrollは別で優先判定されているため、ここでは操作性を優先して
        - 人差し指＋中指が伸展している
        をMouse条件とする。
        """

        index_ext = bool(finger_states.get("index", False))
        middle_ext = bool(finger_states.get("middle", False))
        return index_ext and middle_ext

    @staticmethod
    def _is_scroll_mode(finger_states: dict) -> bool:
        """スクロール（パー）モードの判定。

        仕様書上は「全指伸展」だが、親指は左右判定や姿勢でブレやすい。
        そのため、スクロールの意図（手を大きく開く）を捉える目的で、
        index〜pinkyの4本が伸展していればScrollとして扱う。
        """

        index_ext = bool(finger_states.get("index", False))
        middle_ext = bool(finger_states.get("middle", False))
        ring_ext = bool(finger_states.get("ring", False))
        pinky_ext = bool(finger_states.get("pinky", False))
        return index_ext and middle_ext and ring_ext and pinky_ext

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
    def _contact_distance(hand_landmarks) -> float:
        """親指(4)と「接触候補点」の最小距離（正規化）を返す。

        研究用途メモ:
        - 「中間も含める」方式は接触を拾いやすいが、カーソル操作中の誤検出が出やすい。
        - 「先端のみ」方式は誤検出が減りやすい一方で、閾値（click_threshold）が小さいと
          なかなかONにならないことがある（指先同士でしっかり“つまむ”必要がある）。

        ここでは要求に合わせて、以下の先端(TIP)のみを候補とする:
        - 人差し指TIP(8)
        - 中指TIP(12)
        """

        lm = hand_landmarks
        thumb = lm[4]
        candidates = (8, 12)
        best = float("inf")
        for idx in candidates:
            dx = thumb.x - lm[idx].x
            dy = thumb.y - lm[idx].y
            d = math.sqrt(dx * dx + dy * dy)
            if d < best:
                best = d
        return float(best)

    def _stable_contact(
        self,
        hand_landmarks,
        *,
        click_threshold: float,
        release_threshold: float,
        confirm_frames: int,
    ) -> Tuple[bool, float]:
        """コンタクト判定を安定化して返す。

        不安定になる原因:
        - ランドマークはフレームごとに微小に揺れる
        - 距離が閾値付近だとON/OFFが頻繁に反転してしまう

        対策:
        - 距離をEMAで軽く平滑化
        - ヒステリシス: ONはclick_threshold、OFFはrelease_threshold（通常は少し大きい）
        - confirm_frames: 連続フレームで条件を満たしたときだけ状態遷移する
        """

        dist = self._contact_distance(hand_landmarks)

        # EMA（軽量・低遅延）。係数は固定で小さくし、手ブレだけ抑える。
        alpha = 0.4
        if self._contact_dist_ema is None:
            self._contact_dist_ema = dist
        else:
            self._contact_dist_ema = (alpha * dist) + ((1.0 - alpha) * self._contact_dist_ema)

        d = float(self._contact_dist_ema)
        on_th = float(click_threshold)
        off_th = float(max(release_threshold, on_th))
        need = max(1, int(confirm_frames))

        if not self._contact_state:
            # OFF→ON: しきい値以下が連続したらON
            if d <= on_th:
                self._contact_below_count += 1
            else:
                self._contact_below_count = 0
            if self._contact_below_count >= need:
                self._contact_state = True
                self._contact_above_count = 0
        else:
            # ON→OFF: release_threshold以上が連続したらOFF
            if d >= off_th:
                self._contact_above_count += 1
            else:
                self._contact_above_count = 0
            if self._contact_above_count >= need:
                self._contact_state = False
                self._contact_below_count = 0

        return self._contact_state, d

    def _compute_pointer_xy(self, hand_landmarks, settings: Settings) -> Tuple[float, float]:
        """Mouseモード用のポインタ座標（正規化0..1）を返す。

        仕様:
        - control.pointer_source に応じて算出点を切り替える
          - index_tip: 人差し指先(8)
          - index_middle_avg: 人差し指先(8)と中指先(12)の平均
        - ROIが有効ならROI内で正規化し直し、少ない手の動きで全画面操作しやすくする
        - 返り値のXは「ユーザー視点で直感的」になるよう鏡補正（mirror_x=Trueなら反転）
        """

        lm = hand_landmarks
        src = str(getattr(settings.control, "pointer_source", "index_middle_avg"))
        if src == "index_tip":
            x = float(lm[8].x)
            y = float(lm[8].y)
        else:
            # 互換: 未知値は従来挙動（平均）
            x = (float(lm[8].x) + float(lm[12].x)) / 2.0
            y = (float(lm[8].y) + float(lm[12].y)) / 2.0

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

