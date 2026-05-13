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
- **マニュアル**: `docs/*.md` を分割管理。アプリ内マニュアルタブで Markdown を表示（左にファイル一覧・「すべて」連結表示、語句検索、再読込）。

## macOS: 全画面動画でPieMenuが出ない場合
YouTube / Prime Video / Disney+ などの動画サービスでは、ブラウザやOSが **保護された動画レイヤー（DRM）** を使うことがあり、macOSの仕様として「他アプリのオーバーレイを上に重ねられない」ケースがあります。

- **回避策（推奨順）**
  - **最大化（ネイティブ全画面を使わない）**: ウィンドウ左上の緑ボタンではなく、サイズを広げて最大化で運用する
  - **PiP（ピクチャ・イン・ピクチャ）**: プレイヤーのPiP機能を使う（ブラウザ/サイト側対応が必要）
  - **別スペース/別モニタで操作**: 動画を全画面にするスペースと、操作するスペースを分ける

- **前面表示の切り替え（上級・実験）**  
  ターミナルから起動する場合、`PALMCONTROL_MACOS_OVERLAY=aggressive` に加えて次を試せます。
  - `PALMCONTROL_MACOS_OVERLAY_LEVEL=mainmenu` … `NSMainMenuWindowLevel`（全画面上に出しやすいことがある）
  - 既存: `assistive` / `popup` / `screensaver`（Quartz の `CGWindowLevelForKey`）

上記でも改善しない場合、OS側制約の可能性が高いため、アプリ側での完全な上書きは難しいです。

## 進捗
- **完了**:
  - 設定タブのスクロール対応（項目が多くても操作可能）
  - クリック割当の仕様変更（2/3/4+タップ）
  - ドラッグ開始判定 `control.drag_hold_ms` の追加とGUI連携
  - マニュアルのASCII図撤去＋分割MD化（`docs/00_...`〜`07_...`）
  - マニュアル表示のMarkdown化＋再読込
  - マニュアル: ファイル一覧（目次）・単ファイル表示・語句検索（次へ／折り返し）
  - 設定タブ: 操作感度プリセット（ブラウズ／プレゼン／精密クリック。編集モードで一括投入、適用で保存）
- **次スプリントの主軸（2026-05 時点）**: README にあった「ユーザー向け改善」を主軸にし、**配布・研究**は並行で小さく進める想定。
- **スプリント分割（目安）**:
  - **完了（本リリース相当）**: マニュアル目次・検索、操作感度プリセット（上記「完了」に含む）
  - **次（1〜2週）**: 画面端補正（相対移動の端届き改善を YAML パラメータ化）、マニュアルの全文検索強化（ハイライト等）は任意
  - **その次**: PyInstaller 等のバンドル雛形、権限・アクセシビリティ手順の一本化
  - **研究を継続する場合**: セッションログへの設定ハッシュ付与、オフラインベンチ（固定動画入力）を別スプリントで切り出し
- **次の候補**:
  - 操作: 画面端補正（相対移動の加速度・端ゲイン）
  - 配布: PyInstaller等によるバンドル、署名、権限/アクセシビリティ手順の整備
  - 研究: ログ再現性の強化、オフラインベンチ

## ドキュメント
- **アプリ内マニュアル**: `docs/` 配下の Markdown を、一覧で選んで単体表示するか「すべて」で連結表示します。
- **テストプログラムの起動方法**: `tests/README.md`（カメラ/検出/制御の単体・統合テスト）
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
- **起動（推奨）**: `./scripts/run_mac.command`
  - 初回: `.venv` 作成 → 依存導入 → 起動
  - 2回目以降: `.venv` 再利用 → 起動

ターミナルから実行する場合:

```bash
chmod +x scripts/run_mac.command
./scripts/run_mac.command
```

### Windows
- **起動（推奨）**: `./scripts/run_windows.bat`
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

#### Windowsでカメラが開けない場合
- まず `.\tests\run_test.ps1 camera` でカメラ単体テストを実行してください。
- Windows設定で `プライバシーとセキュリティ > カメラ` を開き、カメラアクセスを許可してください。
- Zoom/Teams/ブラウザなど、他アプリがカメラを占有していないか確認してください。
- このプロジェクトはWindowsで `DirectShow` / `Media Foundation` / `default` の順にカメラバックエンドを自動で試行します。失敗時はエラーメッセージ内の「試行バックエンド」を確認してください。

## 開発向け（手動起動）
venvを手動で作る場合の例です。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.main
```
