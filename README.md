# PalmControl
PalmControlは、PCのカメラを使ってマウス操作やショートカット実行を可能にするハンドトラッキング・ツールです。MediaPipeを活用し、M1 MacやWindowsでも軽量かつリアルタイムに動作。手が離せない作業中や、プレゼンテーション、日常のブラウジングを「空中ジェスチャー」で効率化します。

## 現在の仕様（要点）
- **UI**: PyQt6。設定/モーションテスト/ログ/マニュアルのタブ構成。
- **設定**: `config/settings.yaml` に集約。GUIから主要パラメータを編集可能（YAML再読込あり）。
- **ポインタ移動**: 相対移動（空中トラックパッド方式）。ROIを有効にすると、少ない手の移動で全画面をカバーしやすい。
- **感度**:
  - `control.sensitivity_x` / `control.sensitivity_y`（左右/上下の移動倍率）
  - `control.relative_move_clamp_th`（1フレーム当たり最大Δ。小さいと端まで届きにくい）
  - `control.smoothing_factor`（EMAで追従性/滑らかさのトレードオフ）
- **クリック/タップ**:
  - 左シングル: 連続タップ2回
  - 左ダブル: 連続タップ3回
  - 右クリック: 連続タップ4回以上
- **ドラッグ/範囲選択**: 接触（pinch）長押しで `mouseDown`、離すと `mouseUp`。ドラッグ中はカーソル移動が有効。
- **マウス実行**: `pynput` 優先、失敗時に `pyautogui` をフォールバックとして利用。
- **マニュアル**: `docs/*.md` を分割管理。アプリ内マニュアルタブで Markdown を読み込み表示（再読込ボタンあり）。

## 進捗
- **完了**:
  - 設定タブのスクロール対応（項目が多くても操作可能）
  - クリック割当の仕様変更（2/3/4+タップ）
  - ドラッグ開始判定 `control.drag_hold_ms` の追加とGUI連携
  - マニュアルのASCII図撤去＋分割MD化（`docs/00_...`〜`07_...`）
  - マニュアル表示のMarkdown化＋再読込
- **次の候補**:
  - マニュアル: 目次/ファイル一覧から個別表示、検索
  - 操作: 感度プリセット（プレゼン向け/ブラウズ向け等）、画面端補正
  - 配布: PyInstaller等によるバンドル、署名、権限/アクセシビリティ手順の整備

## ドキュメント
- **アプリ内マニュアル**: `docs/` 配下の Markdown を連結表示します。
- **ファイル一覧**:
  - `docs/00_intro.md`
  - `docs/01_architecture.md`
  - `docs/02_gui.md`
  - `docs/03_gestures.md`
  - `docs/04_mouse_and_scroll.md`
  - `docs/05_clicks_and_drags.md`
  - `docs/06_safety_and_troubleshooting.md`
  - `docs/07_changelog_notes.md`

## 起動方法（初回/2回目以降 共通）
このリポジトリ直下で実行してください（`src/main.py` がエントリポイントです）。

### macOS
- **起動（推奨）**: `scripts/run_mac.command`
  - 初回: `.venv` 作成 → 依存導入 → 起動
  - 2回目以降: `.venv` 再利用 → 起動

ターミナルから実行する場合:

```bash
chmod +x scripts/run_mac.command
./scripts/run_mac.command
```

### Windows
- **起動（推奨）**: `scripts/run_windows.bat`
  - 初回: `.venv` 作成 → 依存導入 → 起動
  - 2回目以降: `.venv` 再利用 → 起動

エクスプローラーから `scripts/run_windows.bat` をダブルクリックして起動できます。

- **起動（PowerShell版 / 推奨）**: `scripts/run_windows.ps1`
  - `.bat` で起動できない場合や、PowerShell運用に揃えたい場合はこちら

PowerShellから実行する場合（ブロックされる場合は実行ポリシーを一時的に緩めます）:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\run_windows.ps1
```

## 開発向け（手動起動）
venvを手動で作る場合の例です。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.main
```
