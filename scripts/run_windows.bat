@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM PalmControl launcher (Windows)
REM - 初回: venv作成 → pip更新 → 依存導入
REM - 2回目以降: venvを再利用して起動

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%\.."

set "PYTHON=py -3"
%PYTHON% --version >nul 2>nul
if errorlevel 1 (
  set "PYTHON=python"
  %PYTHON% --version >nul 2>nul
  if errorlevel 1 (
    echo Python 3 が見つかりません。Python 3 をインストールしてから再実行してください。
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [PalmControl] 初回セットアップ: .venv を作成します
  %PYTHON% -m venv .venv
)

echo [PalmControl] 依存関係を更新します
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo [PalmControl] カメラ事前診断を実行します
python tests\probe_camera.py
if errorlevel 1 (
  echo [PalmControl] カメラ事前診断に失敗しました。設定や占有状態を確認してください。
  echo [PalmControl] Enterで続行、Ctrl+Cで中断できます。
  pause >nul
)

echo [PalmControl] アプリを起動します
python -m src.main

endlocal

