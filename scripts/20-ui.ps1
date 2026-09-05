# 20 - Start Vajren. The single entry point; Vajren.bat calls this.
#
#   .\scripts\20-ui.ps1              native window (default)
#   .\scripts\20-ui.ps1 -Headless    server only, no window (for tests)
#   .\scripts\20-ui.ps1 -Browser     open in the default browser instead
#
# Order matters and each step waits for the last: models -> gateway -> face.
# Starting the face first gives you a window that looks perfect and cannot
# answer, which is the most confusing possible failure.
param([switch]$Headless, [switch]$Browser)
$ErrorActionPreference = "Continue"
$root = "C:\vajren"
$py   = "$root\.venv\Scripts\python.exe"
$pyw  = "$root\.venv\Scripts\pythonw.exe"

# --- 1. free port 7777 -------------------------------------------------------
# Get-Process cannot see command lines, so an old `python -m core.server` is
# invisible to a name match. Ask the socket who owns it instead.
$owner = (Get-NetTCPConnection -LocalPort 7777 -State Listen -EA SilentlyContinue).OwningProcess
foreach ($p in $owner) {
  Write-Host "  stopping the old face (pid $p)" -ForegroundColor DarkGray
  Stop-Process -Id $p -Force -EA SilentlyContinue
}
if ($owner) { Start-Sleep -Seconds 2 }

# --- 2. models + gateway -----------------------------------------------------
$up = $false
try { Invoke-RestMethod "http://127.0.0.1:4000/v1/models" -TimeoutSec 4 | Out-Null; $up = $true } catch {}
if ($up) {
  Write-Host "  models      already running" -ForegroundColor DarkGray
} else {
  Write-Host "  starting models and gateway (~40s)..." -ForegroundColor Cyan
  & "$root\scripts\04-start-stack.ps1" | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
}

# --- 3. the face -------------------------------------------------------------
if ($Headless) {
  Start-Process -FilePath $py -ArgumentList @("-m","core.server") -WorkingDirectory $root `
    -RedirectStandardOutput "$root\logs\face.log" -RedirectStandardError "$root\logs\face.err.log" `
    -WindowStyle Hidden
} elseif ($Browser) {
  Start-Process -FilePath $py -ArgumentList @("-m","core.server") -WorkingDirectory $root `
    -RedirectStandardOutput "$root\logs\face.log" -RedirectStandardError "$root\logs\face.err.log" `
    -WindowStyle Hidden
} else {
  # pythonw: a real window with no console behind it.
  # ⚠ Redirect BOTH streams. pythonw has no console, so without this a crash
  #   leaves absolutely nothing — the window vanishes and the only evidence is
  #   the user saying "it gave an error". That happened once; not twice.
  Start-Process -FilePath $pyw -ArgumentList @("-m","core.app") -WorkingDirectory $root `
    -RedirectStandardOutput "$root\logs\app.log" -RedirectStandardError "$root\logs\app.err.log"
}

$deadline = (Get-Date).AddSeconds(90); $ok = $false
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 700
  try { $s = Invoke-RestMethod "http://127.0.0.1:7777/status" -TimeoutSec 3; $ok = $true; break } catch {}
}
if (-not $ok) { Write-Host "  face did NOT start - see logs\face.err.log" -ForegroundColor Red; exit 1 }

Write-Host ("  face up      " + $(if($Headless){"http://127.0.0.1:7777 (headless)"}
                                 elseif($Browser){"http://127.0.0.1:7777"}
                                 else{"native window"})) -ForegroundColor Green
Write-Host ("  session log  " + $s.log)
Write-Host ("  voice        tts=" + $s.voice.tts + "  stt=" + $s.voice.stt + "  audio=" + $s.voice.audio)
if ($Browser) { Start-Process "http://127.0.0.1:7777" }
