# Start (or restart) the model download in the background.
#
#   .\scripts\resume-download.ps1              reflex only, ~2.3 GB
#   .\scripts\resume-download.ps1 -All         the whole bench, ~43 GB
#
# Safe to run any time. It resumes from whatever is already on disk, retries
# through dropped connections on its own, and keeps running after you close the
# window. Watch it with .\scripts\progress.ps1 -Watch
param([switch]$All)

$root = "C:\vajren"
$py   = (Get-Content "$root\config\python-path.txt").Trim()
New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null

$already = Get-Process python -EA SilentlyContinue | Where-Object { $_.Path -like "*vajren*" }
if ($already) {
  Write-Host "Already running (pid $($already[0].Id)). Watch it with:" -ForegroundColor Yellow
  Write-Host "  .\scripts\progress.ps1 -Watch"
  exit
}

$argList = @("$root\scripts\fetch.py")
if ($All) { $argList += "--all" } else { $argList += "reflex" }

Start-Process -FilePath $py -ArgumentList $argList `
  -RedirectStandardOutput "$root\logs\fetch.log" `
  -RedirectStandardError  "$root\logs\fetch.err.log" `
  -WindowStyle Hidden

Start-Sleep 4
Write-Host "Started in the background. It survives closing this window." -ForegroundColor Green
Write-Host "`nWatch it:" -ForegroundColor Cyan
Write-Host "  cd C:\vajren"
Write-Host "  .\scripts\progress.ps1 -Watch"
