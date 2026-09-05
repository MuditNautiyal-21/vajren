# 16b - Re-fetch the small whisper files from scratch.
#
# WHY: the first attempt used urllib, which failed part-way and left partial
# files. curl -C - then RESUMED those partials — appending the whole file to a
# corrupt prefix. The result has a plausible size and is garbage. That is how
# `UnicodeDecodeError at position 13` happens.
#
# Lesson worth keeping: resume is only safe when you know what the existing
# bytes are. After a failed download from a DIFFERENT tool, delete and start over.
$ErrorActionPreference = "Stop"
$dir = "C:\vajren\models\voice\whisper\small.en"
$hf  = "https://huggingface.co/Systran/faster-whisper-small.en/resolve/main/"

foreach ($f in "config.json", "tokenizer.json", "vocabulary.txt") {
  $p = Join-Path $dir $f
  Remove-Item $p -Force -EA SilentlyContinue
  Write-Host "  fetching $f ..." -ForegroundColor Cyan
  & curl.exe -L --fail --retry 5 --retry-delay 3 --connect-timeout 30 -A vajren -o $p ($hf + $f)
  $len = (Get-Item $p).Length
  Write-Host ("    {0:N0} bytes" -f $len) -ForegroundColor Green
}

Write-Host "`n  first bytes of vocabulary.txt (should be readable words):" -ForegroundColor Cyan
Get-Content (Join-Path $dir "vocabulary.txt") -TotalCount 3 | ForEach-Object { Write-Host "    $_" }
