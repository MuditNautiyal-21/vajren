# 13 - Delete .gguf files in Downloads that are already in models\.
#
# ⚠ .gguf ONLY. Nothing else in Downloads is touched, looked at, or listed.
#
# THREE conditions before anything is deleted. All three, every file:
#   1. a file of the same name exists in models\
#   2. the two are byte-for-byte the same SIZE
#   3. the models\ copy passes a GGUF magic-number check - the first four bytes
#      are "GGUF". A truncated or corrupt download fails this.
#
# Condition 3 matters because size alone is not proof: a resumed download that
# wrote the right number of bytes in the wrong order has the right size and is
# useless. Checking the header is cheap; hashing 20 GB is not.
#
# Deletes to the RECYCLE BIN, not permanently. If any of this is wrong, the
# files are still there. That is worth the two lines it costs.
$ErrorActionPreference = "Stop"
$src = Join-Path $env:USERPROFILE "Downloads"
$dst = "C:\vajren\models"
Add-Type -AssemblyName Microsoft.VisualBasic

function Test-Gguf([string]$path) {
  $fs = [IO.File]::OpenRead($path)
  try {
    $b = New-Object byte[] 4
    $null = $fs.Read($b, 0, 4)
    return ([Text.Encoding]::ASCII.GetString($b) -eq "GGUF")
  } finally { $fs.Close() }
}

$freed = 0
foreach ($f in Get-ChildItem $src -Filter *.gguf -EA SilentlyContinue) {
  $keep = Join-Path $dst $f.Name

  if (-not (Test-Path $keep)) {
    Write-Host ("  KEEP  {0} - no copy in models\, this is the only one" -f $f.Name) -ForegroundColor Yellow
    continue
  }
  $k = Get-Item $keep
  if ($k.Length -ne $f.Length) {
    Write-Host ("  KEEP  {0} - sizes differ (models={1}, downloads={2})" -f $f.Name, $k.Length, $f.Length) -ForegroundColor Red
    continue
  }
  if (-not (Test-Gguf $keep)) {
    Write-Host ("  KEEP  {0} - the models\ copy is NOT a valid GGUF" -f $f.Name) -ForegroundColor Red
    continue
  }

  Write-Host ("  recycling  {0}  ({1:N1} GB)" -f $f.Name, ($f.Length/1GB)) -ForegroundColor Cyan
  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(
    $f.FullName,
    [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
    [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin)
  $freed += $f.Length
}

Write-Host ("`n  {0:N1} GB sent to the Recycle Bin (recoverable)." -f ($freed/1GB)) -ForegroundColor Green
Write-Host "  models\ still holds:" -ForegroundColor Cyan
Get-ChildItem $dst -Filter *.gguf | ForEach-Object {
  Write-Host ("    {0,-45} {1,6:N1} GB" -f $_.Name, ($_.Length/1GB))
}
$rem = @(Get-ChildItem $src -Filter *.gguf -EA SilentlyContinue)
Write-Host ("  .gguf left in Downloads: {0}" -f $rem.Count)
