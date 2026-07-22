# Repo metrics — file count, LOC, source/asset/bin sizes. Pure filesystem, no deps.
# Run from repo root:  powershell -File benchmarks\measure_repo.ps1
$root = Split-Path $PSScriptRoot -Parent

Write-Host "=== python files / LOC (excl venv/dist/build/pycache) ==="
$py = Get-ChildItem $root -Recurse -Filter *.py -File |
      Where-Object { $_.FullName -notmatch '\\(\.venv|dist|build|__pycache__)\\' }
$loc = ($py | Get-Content | Measure-Object -Line).Lines
"python files: $($py.Count) | total LOC: $loc"
foreach ($area in 'toolbox','tools') {
  $f = $py | Where-Object { $_.FullName -match "\\$area\\" }
  $l = ($f | Get-Content | Measure-Object -Line).Lines
  "{0,-10} {1,3} files  {2,5} LOC" -f $area, $f.Count, $l
}

Write-Host "`n=== assets ==="
Get-ChildItem "$root\assets" -Recurse -File |
  ForEach-Object { "{0,-28} {1,8:N0} KB" -f $_.Name, ($_.Length/1KB) }

Write-Host "`n=== bundled bin (external binaries) ==="
if (Test-Path "$root\bin") {
  Get-ChildItem "$root\bin" -File | ForEach-Object { "{0,-20} {1,7:N1} MB" -f $_.Name, ($_.Length/1MB) }
  $sum = (Get-ChildItem "$root\bin" -Recurse -File | Measure-Object Length -Sum).Sum/1MB
  "{0,-20} {1,7:N1} MB (total)" -f "bin/", $sum
} else { "no bundled bin/" }
