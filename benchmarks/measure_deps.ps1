# Dependency footprint — installs base vs Clean Cutout stacks into an isolated
# temp target (never touches the app venv) and measures real installed size +
# the biggest sub-packages. Requires uv on PATH.
#   powershell -File benchmarks\measure_deps.ps1
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = "H:\Apps\scoop\shims\uv.exe" }
$tmp = Join-Path $env:TEMP "kstb_deps_$(Get-Random)"

function Measure-Target($name, $pkgs) {
  $t = Join-Path $tmp $name
  New-Item -ItemType Directory -Force -Path $t | Out-Null
  & $uv pip install --target $t --quiet @pkgs 2>&1 | Out-Null
  $sz = (Get-ChildItem $t -Recurse -File | Measure-Object Length -Sum).Sum/1MB
  "{0}: {1:N1} MB" -f $name, $sz
  Get-ChildItem $t -Directory | ForEach-Object {
    $s = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum/1MB
    [PSCustomObject]@{ Pkg = $_.Name; MB = [math]::Round($s,1) }
  } | Where-Object { $_.MB -ge 1 } | Sort-Object MB -Descending | Select-Object -First 12 |
      Format-Table -AutoSize | Out-String
}

Write-Host "=== BASE deps (shell + deterministic tools) ==="
Measure-Target "base" @("customtkinter==5.2.2","send2trash>=1.8")
Write-Host "=== CLEAN_CUTOUT full stack (the only AI tool) ==="
Measure-Target "cutout" @("rembg>=2.0","onnxruntime>=1.17","pillow>=10.0","numpy>=1.26","numba>=0.59","llvmlite>=0.42")
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
