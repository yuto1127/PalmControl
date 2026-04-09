## ジェスチャー図解（簡易）

このページは「どの指の形で、どのモードになり、何が起きるか」を素早く参照するためのメモです。  
カメラの見え方・個人差・環境光で判定が揺れるため、最終的には `config/settings.yaml` の調整が前提です。

---

## 1) モード切り替え（基本）

PalmControlは、手のランドマークから「指が伸びている/曲がっている」を推定し、モードを切り替えます。

- **Mouse（マウス）**: 人差し指＋中指が伸びている状態を中心に判定  
- **Scroll（スクロール）**: 人差し指/中指/薬指/小指が伸びている状態を中心に判定  
- **None**: それ以外（グー等）→ 基本的に操作しない

※ 実装は研究用途のため、今後ロジックや閾値が変わる可能性があります。

---

## 2) Mouse（マウス）モード

### 2-1. カーソル移動（空中トラックパッド）

指の形（目安）:

```
  [index]  [middle]   ring  pinky
     |        |        .     .
     |        |        .     .
  (伸)      (伸)     (不問) (不問)
```

動作:
- 手を動かした「差分」でカーソルが動きます（手の位置に吸い寄せない設計）。

調整:
- `control.sensitivity_x` / `control.sensitivity_y`（移動量）
- `control.smoothing_factor`（滑らかさ）
- `control.relative_move_deadzone`（手ブレ無視）
- `control.relative_move_clamp_th`（ジャンプ抑制）
- `camera.roi.*`（手を動かす有効範囲）

---

### 2-2. クリック姿勢（誤操作防止のキー）

「つまむ」動作はカーソルがブレやすいので、クリック意図の分離として「中指を曲げる」を条件にできます。

`control.click_requires_middle_bent: true` の場合（推奨）:

```
  index   middle
   |       _
   |      (曲)
 (伸)     (曲)
  \____/
  pinch（親指でつまむ）
```

動作:
- クリック/ドラッグの判定が有効になります。

調整:
- `control.click_threshold`（接触距離）
- `control.tap_interval_ms`（連続タップ間隔）
- `control.move_suppress_on_middle_bent`（クリック姿勢に入ったら移動抑制）

---

### 2-3. タップ / ダブルタップ / 右クリック / ドラッグ（概念）

接触（pinch）のON/OFF履歴で決まります。

- **短い接触**: タップ（回数でクリック種別が変化）
- **長い接触**: ドラッグ（開始→維持→離すと終了）

目安:

```
接触:  ON  OFF          → 1回（タップ）
接触:  ON  OFF  ON OFF  → 2回（ダブル相当）
接触:  ON  OFF  ON OFF  ON OFF → 3回（右クリック相当 など）
接触:  ON------OFF      → ドラッグ
```

※ 実際の割り当て（何回で左/右になるか）は `controller` の実装仕様に依存します。

クリック時のズレが気になる場合:
- `control.cursor_anchoring.enabled: true` を推奨
- `control.cursor_anchoring.pre_contact_threshold` を少し大きめにする（予兆で早めに固定）

---

## 3) Scroll（スクロール）モード

指の形（目安）:

```
 index  middle  ring  pinky
   |      |      |     |
  (伸)   (伸)   (伸)  (伸)
 thumb は不問
```

動作:
- 手のひら中心の上下移動（Y変位）をスクロール量に変換します。

調整:
- `control.scroll_sensitivity`（速度）
- `control.scroll_deadzone`（勝手スクロール防止）

---

## 4) None（何もしない）

例:
- **グー（握りこぶし）**
- 指が伸びたり曲がったりして判定が安定しない状態

目的:
- 意図しない操作を起こさない（安全側）

---

## 5) よくある症状と調整ガイド（最短）

- カーソルが細かく揺れる:
  - `control.relative_move_deadzone` を少し上げる
  - `control.smoothing_factor` を少し下げる（滑らか寄り）
- 動かすのに手の移動が大きすぎる:
  - `control.sensitivity_x/y` を上げる
  - `camera.roi.w/h` を少し下げる（中心寄りにして少ない動きでカバー）
- クリック時にカーソルがズレる:
  - `control.cursor_anchoring.enabled: true`
  - `control.cursor_anchoring.pre_contact_threshold` を少し上げる
  - `control.move_suppress_on_middle_bent: true`
- 勝手にスクロールする:
  - `control.scroll_deadzone` を上げる
  - `control.scroll_sensitivity` を下げる

