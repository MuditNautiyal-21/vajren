# Move any .gguf sitting in Downloads into models\, skipping ones already there.
# Verifies by SIZE before removing a duplicate - never deletes on filename alone.
$ErrorActionPreference = "Continue"
$src = Join-Path $env:USERPROFILE "Downloads"
$dst = "C:\vajren\models"

foreach ($f in Get-ChildItem $src -Filter *.gguf -EA SilentlyContinue) {
  $target = Join-Path $dst $f.Name
  if (Test-Path $target) {
    $have = (Get-Item $target).Length
    if ($have -eq $f.Length) {
      Write-Host ("  duplicate (identical size)  {0}  [{1:N1} GB reclaimable]" -f $f.Name, ($f.Length/1GB)) -ForegroundColor Yellow
    } else {
      Write-Host ("  CONFLICT  {0}  models={1} downloads={2} - left alone" -f $f.Name, $have, $f.Length) -ForegroundColor Red
    }
    continue
  }
  Write-Host ("  moving  {0}  ({1:N1} GB)..." -f $f.Name, ($f.Length/1GB)) -ForegroundColor Cyan
  Move-Item -LiteralPath $f.FullName -Destination $target -Force
  if (Test-Path $target) { Write-Host "    ok" -ForegroundColor Green }
}

Write-Host "`nmodels\ now holds:" -ForegroundColor Cyan
Get-ChildItem $dst -Filter *.gguf | ForEach-Object {
  Write-Host ("  {0,-45} {1,6:N1} GB" -f $_.Name, ($_.Length/1GB))
}
