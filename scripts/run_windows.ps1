Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# PalmControl launcher (Windows / PowerShell)
# - 初回: venv作成 → pip更新 → 依存導入
# - 2回目以降: venvを再利用して起動

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

function Get-PythonCommand {
  # Prefer Python Launcher on Windows if available
  if (Get-Command py -ErrorAction SilentlyContinue) { return @("py", "-3") }
  if (Get-Command python -ErrorAction SilentlyContinue) { return @("python") }
  throw "Python 3 が見つかりません。Python 3 をインストールしてから再実行してください。"
}

$py = Get-PythonCommand

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  Write-Host "[PalmControl] 初回セットアップ: .venv を作成します"
  & $py[0] @($py[1..($py.Length-1)]) -m venv .venv
}

Write-Host "[PalmControl] 依存関係を更新します"
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host "[PalmControl] アプリを起動します"
& ".\.venv\Scripts\python.exe" -m src.main

