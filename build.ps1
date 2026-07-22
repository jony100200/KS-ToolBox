# build.ps1 — one-command portable build of KS ToolBox (Windows).
#   powershell -File build.ps1
# Produces dist\KS ToolBox\ (a portable folder with a bundled Python — no install
# needed by the end user). Uses the app's dedicated .venv as the build env so the
# bundle contains only KS ToolBox's own dependencies.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv found. Create it and install requirements first." }

Write-Host "=== ensuring PyInstaller is available ==="
& $py -m pip install --quiet --disable-pip-version-check pyinstaller 2>&1 | Select-Object -Last 2

Write-Host "=== cleaning previous build ==="
Remove-Item (Join-Path $root "build") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $root "dist")  -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "=== building (one-folder, windowed = no console) ==="
& $py -m PyInstaller "$root\KS ToolBox.spec" --noconfirm --distpath "$root\dist" --workpath "$root\build"

$exe = Join-Path $root "dist\KS ToolBox\KS ToolBox.exe"
if (Test-Path $exe) {
  Write-Host "`nBuild OK: $exe"
  & powershell -NoProfile -File (Join-Path $root "benchmarks\measure_package.ps1")
  Write-Host "`nShip the whole 'dist\KS ToolBox' folder (zip it for a portable download)."
  Write-Host "Remember to include FFmpeg's license per THIRD_PARTY_NOTICES.md."
} else {
  throw "Build failed — no exe produced."
}
