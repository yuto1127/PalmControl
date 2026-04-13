Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

param(
  [Parameter(Mandatory=$false, Position=0)]
  [ValidateSet("camera","detection","control")]
  [string]$Target = "camera"
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

switch ($Target) {
  "camera"    { $scriptPath = "tests/test_camera.py" }
  "detection" { $scriptPath = "tests/test_detection.py" }
  "control"   { $scriptPath = "tests/test_control.py" }
}

$venvPy = ".\.venv\Scripts\python.exe"
if (Test-Path $venvPy) {
  & $venvPy $scriptPath
} else {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $scriptPath
  } else {
    & python $scriptPath
  }
}

