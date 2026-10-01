# build.ps1 - one-command portable build of KS ToolBox (Windows).
#   powershell -File build.ps1
# Produces dist\KS ToolBox\ (a portable folder with a bundled Python - no install
# needed by the end user). Uses the app's dedicated .venv as the build env so the
# bundle contains only KS ToolBox's own dependencies.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
# Release builds should use a clean environment with only requirements.txt + requirements-build.txt,
# so optional extras installed in .venv are not swept into the bundle. Set KS_BUILD_PYTHON to that python.exe.
$py = if ($env:KS_BUILD_PYTHON) { $env:KS_BUILD_PYTHON } else { Join-Path $root ".venv\Scripts\python.exe" }
if (-not (Test-Path $py)) { throw "No .venv found. Create it and install requirements first." }

Write-Host "=== verifying pinned build tooling ==="
& $py -c "import PyInstaller; assert PyInstaller.__version__ == '6.21.0', PyInstaller.__version__"
if ($LASTEXITCODE -ne 0) {
  throw "Install the pinned build tools: uv pip install --python `"$py`" -r `"$root\requirements-build.txt`""
}

Write-Host "=== cleaning previous build ==="
Remove-Item (Join-Path $root "build") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $root "dist")  -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "=== building (one-folder, windowed = no console) ==="
& $py -m PyInstaller "$root\KS ToolBox.spec" --noconfirm --distpath "$root\dist" --workpath "$root\build"

$exe = Join-Path $root "dist\KS ToolBox\KS ToolBox.exe"
if (Test-Path $exe) {
  Write-Host "`n=== bundling external media tools ==="
  $releaseBin = Join-Path $root "dist\KS ToolBox\bin"
  New-Item -ItemType Directory -Force -Path $releaseBin | Out-Null
  foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
    $source = Join-Path $root "bin\$name"
    if (-not (Test-Path $source)) {
      throw "Portable release requires $source"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $releaseBin $name) -Force
  }

  Write-Host "`n=== bundling optional Real-ESRGAN NCNN AI-upscale runtime (if present) ==="
  $esrganSrc = Join-Path $root "models\realesrgan-ncnn-20220424"
  $esrganExe = Join-Path $esrganSrc "realesrgan-ncnn-vulkan.exe"
  if (Test-Path $esrganExe) {
    # Exe-adjacent, mirroring bin/ -- matches bundled_models_dir() in toolbox/engine_common.py,
    # NOT the PyInstaller _internal/ staging dir (a frozen module's __file__ lives one level
    # deeper than the exe, so models/ must sit beside "KS ToolBox.exe", not inside _internal/).
    $esrganDest = Join-Path $root "dist\KS ToolBox\models\realesrgan-ncnn-20220424"
    New-Item -ItemType Directory -Force -Path (Join-Path $esrganDest "models") | Out-Null
    Copy-Item -LiteralPath $esrganExe -Destination $esrganDest -Force
    foreach ($dll in @("vcomp140.dll", "vcomp140d.dll")) {
      $dllSrc = Join-Path $esrganSrc $dll
      if (Test-Path $dllSrc) { Copy-Item -LiteralPath $dllSrc -Destination $esrganDest -Force }
    }
    Copy-Item -Path (Join-Path $esrganSrc "models\*.bin") -Destination (Join-Path $esrganDest "models") -Force
    Copy-Item -Path (Join-Path $esrganSrc "models\*.param") -Destination (Join-Path $esrganDest "models") -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $root "dist\KS ToolBox\licenses") | Out-Null
    Copy-Item -LiteralPath (Join-Path $root "licenses\RealESRGAN-NCNN-BSD-3-Clause.txt") `
      -Destination (Join-Path $root "dist\KS ToolBox\licenses\RealESRGAN-NCNN-BSD-3-Clause.txt") -Force
    Write-Host "Bundled Real-ESRGAN NCNN AI upscale runtime (models/realesrgan-ncnn-20220424)."
  } else {
    Write-Host "No models/realesrgan-ncnn-20220424 found in the source tree -- shipping without AI upscale (deterministic tools still work)."
  }

  Write-Host "`n=== generating notices, dependency manifest, and SPDX SBOM ==="
  $previousPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = $root
    & $py -m benchmarks.build_release_compliance `
      --dist (Join-Path $root "dist\KS ToolBox") `
      --analysis-toc (Join-Path $root "build\KS ToolBox\Analysis-00.toc") `
      --require-ffmpeg
    if ($LASTEXITCODE -ne 0) {
      throw "Release compliance gate failed."
    }
  } finally {
    $env:PYTHONPATH = $previousPythonPath
  }

  Write-Host "`nBuild OK: $exe"
  & powershell -NoProfile -File (Join-Path $root "benchmarks\measure_package.ps1")
  Write-Host "`nShip the whole 'dist\KS ToolBox' folder (zip it for a portable download)."
  Write-Host "Publish corresponding FFmpeg source access beside every binary download."
} else {
  throw "Build failed - no exe produced."
}
