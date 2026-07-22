# Built-package size — run AFTER a PyInstaller build to measure the shippable dist.
#   powershell -File benchmarks\measure_package.ps1
$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root "dist\KS ToolBox"
if (-not (Test-Path $dist)) {
  "No build found at: $dist"
  "Build first:  .venv\Scripts\pyinstaller.exe 'KS ToolBox.spec'"
  return
}
$files = Get-ChildItem $dist -Recurse -File
$total = ($files | Measure-Object Length -Sum).Sum/1MB
"dist size : {0:N1} MB across {1} files" -f $total, $files.Count
Write-Host "`n=== largest 15 files ==="
$files | Sort-Object Length -Descending | Select-Object -First 15 |
  ForEach-Object { "{0,8:N1} MB  {1}" -f ($_.Length/1MB), $_.FullName.Substring($dist.Length+1) }
Write-Host "`n=== bin/ (bundled binaries) ==="
$bin = Join-Path $dist "bin"
if (Test-Path $bin) {
  $b = (Get-ChildItem $bin -Recurse -File | Measure-Object Length -Sum).Sum/1MB
  "{0:N1} MB" -f $b
} else { "no bin/ in dist" }
