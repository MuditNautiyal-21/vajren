# 20 - Bring up the face.
#
#   1. model stack (skipped if :4000 already answers)
#   2. core.server on 127.0.0.1:7777
#   3. the page, in a chromeless APP WINDOW - no tabs, no address bar. It is
#      still your browser engine underneath, which is exactly why the mic,
#      the speaker and the GPU-drawn canvas all just work.
#
# Loopback only. The face is a mouth, an ear and a display for a process that
# holds every capability Vajren has; it is not exposed to the LAN.
param([switch] $NoBrowser)
$ErrorActionPreference = "Continue"
$root = "C:\vajren"
Set-Location $root

$up = $false
try { Invoke-RestMethod "http://127.0.0.1:4000/v1/models" -TimeoutSec 3 | Out-Null; $up = $true } catch {}
if (-not $up) {
  Write-Host "  model stack not running - starting it (about 40s)..." -ForegroundColor Cyan
  & "$root\scripts\04-start-stack.ps1"
}

# Get-Process has no CommandLine; Win32_Process does. Kill only OUR python.
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
  Where-Object { $_.CommandLine -like "*core.server*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
$env:PYTHONIOENCODING = "utf-8"
Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList @("-m", "core.server") `
  -WorkingDirectory $root -RedirectStandardOutput "$root\logs\server.log" `
  -RedirectStandardError "$root\logs\server.err.log" -WindowStyle Hidden

$ok = $false
foreach ($i in 1..30) {
  Start-Sleep -Milliseconds 500
  try { $s = Invoke-RestMethod "http://127.0.0.1:7777/status" -TimeoutSec 2; $ok = $true; break } catch {}
}
if (-not $ok) { Write-Host "  face did not come up - see logs\server.err.log" -ForegroundColor Red; exit 1 }
Write-Host ("  face up      http://127.0.0.1:7777") -ForegroundColor Green
Write-Host ("  session log  {0}" -f $s.log) -ForegroundColor DarkGray
Write-Host ("  voice        tts={0} stt={1} audio={2}" -f $s.voice.tts, $s.voice.stt, $s.voice.audio)

if ($NoBrowser) { exit 0 }
$url = "http://127.0.0.1:7777"
$edge   = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
$exe = if (Test-Path $chrome) { $chrome } elseif (Test-Path $edge) { $edge } else { $null }
if ($exe) {
  # --app: a window with no browser chrome. --autoplay-policy: let Vajren speak
  # without a click first. A dedicated profile dir keeps the mic permission
  # remembered without touching the user's real browser profile.
  Start-Process -FilePath $exe -ArgumentList @("--app=$url", "--autoplay-policy=no-user-gesture-required",
    "--user-data-dir=$root\logs\face-profile", "--window-size=1380,860", "--no-first-run")
} else {
  Start-Process $url
}
