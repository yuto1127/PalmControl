## PalmControl 操作マニュアル（研究用）

このドキュメントは、PalmControlの基本的な起動方法、GUIの使い方、ジェスチャー（操作姿勢）と設定項目の意味をまとめたものです。  
研究用途のため、操作感は `config/settings.yaml` の調整で変化します。

---

## 安全に使うための注意

- **最初は必ずOS操作をOFFのまま**動作確認してください。
  - GUIの「モーションテスト」タブで映像・モード判定が安定してからONにしてください。
- 予期しないクリック/ドラッグが発生する可能性があります。
  - 誤操作が起きた場合は、GUIの **OS操作OFF** に戻してください。
- macOSの環境によっては、カメラ権限（プライバシー）設定が必要です。

---

## 起動方法

仮想環境（`.venv`）を推奨します。

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.main
```

---

## GUIの使い方

### 設定タブ

- **YAML再読込**: `config/settings.yaml` をディスクから読み直します（手動編集した場合に使用）。
- **OS操作を有効化（危険）**: ONにすると、検出結果に基づいてOSのマウス操作が行われます。
- **アプリケーション終了**: 常駐を含めてアプリを終了します。

### モーションテストタブ

- **プレビュー表示（重い）**:
  - ON: カメラ映像＋ランドマーク＋状態表示を描画します（CPU負荷が増えます）。
  - OFF: 描画を止めます（解析と制御は継続可能）。
- **OS操作を有効化（危険）**:
  - ON: マウス移動/クリック/スクロールが実際に発火します。
  - OFF: 解析は行いますがOS操作はしません。

### ログタブ

- `logs/events.jsonl` の先頭（最新）から一定行数を表示します。
- 研究の再現性のため、ログは基本的に即時flushされます（`logging.flush`）。

### マニュアルタブ

- `docs/*.md` を読み込み、内容を表示します。

---

## ジェスチャー（操作姿勢）

PalmControlはカメラ映像から手の骨格（ランドマーク）を推定し、指の状態でモードを切り替えます。

### モード概要

- **Mouse（マウス）**: 主にカーソル移動＋クリック/ドラッグ
- **Scroll（スクロール）**: 上下スクロール
- **None**: 何もしない（移動/スクロール/クリックなし）

### Mouse（マウス）モード

- 目安: **人差し指＋中指が伸びている**状態でMouseになりやすい設計です。
- カーソル移動は「手の位置の絶対座標」ではなく、**フレーム間の差分（空中トラックパッド）**として動きます。
  - これにより「手の位置へカーソルが吸い寄せられる」挙動を減らします。

#### クリック姿勢（誤操作防止）

設定 `control.click_requires_middle_bent: true` の場合、
- **中指を曲げた状態**（middleが伸びていない）でクリック系（タップ/ドラッグ）を許可します。

意図:
- ただ「つまむ」だけだと、カーソル移動とクリックが干渉しやすいため、クリック意図を分離します。

### Scroll（スクロール）モード

- 目安: **人差し指/中指/薬指/小指が伸びている**状態でScrollになりやすい設計です。
- スクロール量は「手のひら中心のY変位」から計算し、感度で倍率をかけます。

---

## クリック/ドラッグの動作（Action Queueing）

クリック判定は「接触（pinch）」のON/OFF履歴から決定します。

- **接触が短い**: タップとしてカウント
- **接触が長い**: ドラッグ開始/継続

連続タップの判定には `control.tap_interval_ms` を使います。  
タップがゆっくりな人は **大きめ（例: 600ms以上）** にすると認識しやすくなります。

---

## 重要設定の意味（抜粋）

### カメラ

- `camera.width / camera.height`: 解像度（下げると軽くなる）
- `camera.fps`: 目標FPS（高すぎると負荷増）
- `camera.roi.*`: 操作有効範囲（中心だけ使うと安定しやすい）

### 検出

- `detection.min_detection_confidence`: 初回検出のしきい値
- `detection.min_tracking_confidence`: 追跡のしきい値
- `detection.frame_skip`: 解析の間引き（軽量化）

### 操作（移動）

- `control.sensitivity_x / sensitivity_y`: カーソル移動の倍率（左右/上下）
- `control.smoothing_factor`: 平滑化（EMA）係数
- `control.relative_move_deadzone`: 微小ブレ無視（大きいほど安定、動き出しは重め）
- `control.relative_move_clamp_th`: 1フレームのジャンプ抑制

### 操作（クリック）

- `control.click_threshold`: 接触（pinch）の距離しきい値
- `control.cursor_anchoring.*`: クリック時のカーソル固定（精度優先）

### 操作（スクロール）

- `control.scroll_sensitivity`: スクロール感度（大きいほど速い）
- `control.scroll_deadzone`: 勝手スクロール防止の死域

### ログ

- `logging.max_bytes / backup_count`: ローテーション設定
- `logging.flush`: 即時flush（研究ログ欠落の防止）

