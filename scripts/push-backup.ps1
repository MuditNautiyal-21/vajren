# Push to both remotes. Run at the end of a session.
#
#   origin -> github.com/MuditNautiyal-21/vajren   (offsite copy)
#   backup -> F:\Programs\AI\VAJREN.git            (bare repo on the T7, offline recovery)
#
# The T7 copy is the one that matters if this PC is reformatted or dies:
#   git clone F:\Programs\AI\VAJREN.git C:\Users\<you>\vajren
#   .\scripts\01-setup-python.ps1 ; .\scripts\02-get-runtime.ps1 ; .\scripts\03-get-models.ps1
$ErrorActionPreference = "Continue"
Set-Location "C:\Users\ytdek\vajren"

$dirty = git status --porcelain
if ($dirty) {
  Write-Host "Uncommitted changes - commit first:" -ForegroundColor Yellow
  git status --short
  exit 1
}

foreach ($r in @("origin", "backup")) {
  Write-Host "`npushing to $r ..." -ForegroundColor Cyan
  $out = git push $r main 2>&1
  if ($LASTEXITCODE -eq 0) { Write-Host "  ok" -ForegroundColor Green }
  else { Write-Host "  FAILED" -ForegroundColor Red; $out | Select-Object -Last 4 }
}

# The T7 may be unplugged. That is fine - but say so loudly rather than silently.
if (-not (Test-Path "F:\Programs\AI\VAJREN.git\HEAD")) {
  Write-Host "`n  ! T7 not present - the offline recovery copy is now STALE." -ForegroundColor Yellow
  Write-Host "    Plug it in and re-run this script." -ForegroundColor Yellow
}
