# tests（単体/統合テストの起動方法）

このフォルダには、PalmControlの研究・動作確認用スクリプトが入っています。  
基本は **リポジトリルート**で実行してください（推奨）。

## 事前準備
- 依存関係が未導入の場合は、先にリポジトリルートの起動スクリプトでセットアップしてください。
  - macOS: `scripts/run_mac.command`
  - Windows: `scripts/run_windows.bat` または `scripts/run_windows.ps1`

## どれを起動すればいい？
- **カメラが開けるか**: `test_camera.py`
- **手の検出（骨格表示）が動くか**: `test_detection.py`
- **検出→マウス制御まで通るか（危険）**: `test_control.py`

## 起動コマンド（推奨：run_test）

### macOS / Linux（bash）

```bash
./tests/run_test.sh camera
./tests/run_test.sh detection
./tests/run_test.sh control
```

### Windows（PowerShell）

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tests\run_test.ps1 camera
.\tests\run_test.ps1 detection
.\tests\run_test.ps1 control
```

### Windows（cmd / .bat）

```bat
tests\run_test.bat camera
tests\run_test.bat detection
tests\run_test.bat control
```

## 直接起動（run_testを使わない場合）
リポジトリルートで実行してください。

```bash
python tests/test_camera.py
python tests/test_detection.py
python tests/test_control.py
```

（venvを使う場合は `python` を `.venv` のpythonに置き換えてください）

## 各スクリプトの説明

### `tests/test_camera.py`
- OpenCVでカメラが開けるか、解像度/FPSを確認します。
- 終了: ウィンドウ上で `q`
- 注意: macOSは初回にカメラ権限が必要な場合があります。

### `tests/test_detection.py`
- MediaPipeで手を検出し、ランドマーク（骨格）をオーバーレイ表示します。
- 初回は `models/hand_landmarker.task` が無い場合に自動ダウンロードします。
- 終了: ウィンドウ上で `q`

### `tests/test_control.py`（危険）
- 検出（detector）と制御（controller）を繋いだ統合テストです。
- **OSのマウス操作が発生する可能性があります。**
- 安全停止: **Esc**（最優先） / 終了: ウィンドウ上で `q`

