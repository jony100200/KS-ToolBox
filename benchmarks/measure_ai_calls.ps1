# AI / network call-site sweep — proves how many AI integrations and outbound
# calls exist in the source. Static, read-only.
#   powershell -File benchmarks\measure_ai_calls.ps1
$root = Split-Path $PSScriptRoot -Parent
$patterns = @('rembg','onnxruntime','new_session','InferenceSession','torch',
              'import numba','whisper','llama','comfy','requests\.','urllib','socket\.',
              'openai','anthropic','http://','https://')
$files = Get-ChildItem "$root\tools","$root\toolbox" -Recurse -Filter *.py -File

Write-Host "=== AI / model / network call sites (code, excluding comments) ==="
$hits = Select-String -Path $files.FullName -Pattern $patterns |
        Where-Object { $_.Line.Trim() -notmatch '^\s*#' -and $_.Line -notmatch '^\s*(""")|(\*)' }
if ($hits) {
  $hits | ForEach-Object { "{0}:{1}: {2}" -f (Split-Path $_.Path -Leaf), $_.LineNumber, $_.Line.Trim() }
  Write-Host "`nfiles with AI/network references:"
  $hits | Group-Object { Split-Path $_.Path -Parent | Split-Path -Leaf } |
    ForEach-Object { "  {0}: {1} lines" -f $_.Name, $_.Count }
} else { "no matches - 0 AI/network call sites" }
