# マウス移動・スクロールの調整

この章では、操作感に効きやすいパラメータを用途別に整理します。

## マウス移動（感度・滑らかさ・ノイズ）

- **`control.sensitivity_x` / `control.sensitivity_y`**: 左右/上下の移動倍率です。画面端まで届かない場合は上げます。
- **`control.smoothing_factor`**: 指数移動平均（EMA）の係数です。大きいほど追従が速く、小さいほど滑らかになります。
- **`control.relative_move_deadzone`**: 微小な手ブレを無視する閾値です。静止時の震えが気になるほど大きくします（動き出しは重くなりがち）。
- **`control.relative_move_clamp_th`**: 1フレームあたりの移動量上限です。検出が飛ぶ場合に小さくすると安定します。
- **`control.mouse_mode_stable_frames`**: Mouseモードを確定するまでの連続フレーム数です。誤判定で動き出す場合は増やします。

## スクロール

- **`control.scroll_sensitivity`**: スクロール速度（倍率）です。
- **`control.scroll_deadzone`**: 微小な上下ブレで勝手にスクロールしないための死域です。

## ROI（操作有効範囲）

`camera.roi.*` は、カメラ画像のどの矩形領域を操作に使うかを正規化座標で指定します。中心部だけを使うと、手の移動量を抑えつつ画面全体を扱いやすくなることがあります。
