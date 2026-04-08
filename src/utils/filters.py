from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class EMAFilter:
    """指数移動平均（EMA）フィルタ。

    目的:
    - 手のランドマーク由来の座標はフレームごとに微小に揺れる（ジッタ）。
    - そのままカーソルへ反映すると視認できる震えになり、操作感が悪化する。
    - EMAは「低コストで滑らかにする」ための基本手法で、研究用途のパラメータ調整にも向く。

    トレードオフ:
    - `alpha` を小さくすると滑らかだが遅延が増える。
    - `alpha` を大きくすると追従は良いがジッタが残る。

    使い方:
    - update(x, y) に生座標を入れると、平滑化後の座標を返す。
    - reset() で内部状態を破棄できる（モード切替・安全停止などで有用）。
    """

    alpha: float
    _x: Optional[float] = None
    _y: Optional[float] = None

    def reset(self) -> None:
        """内部状態をリセットする。"""

        self._x = None
        self._y = None

    def update(self, x: float, y: float) -> Tuple[float, float]:
        """新しい入力でEMAを更新し、平滑化後座標を返す。"""

        a = float(self.alpha)
        if not (0.0 < a <= 1.0):
            raise ValueError(f"EMA alpha は (0, 1] である必要があります: {a}")

        if self._x is None or self._y is None:
            self._x = float(x)
            self._y = float(y)
            return (self._x, self._y)

        self._x = (a * float(x)) + ((1.0 - a) * self._x)
        self._y = (a * float(y)) + ((1.0 - a) * self._y)
        return (self._x, self._y)

