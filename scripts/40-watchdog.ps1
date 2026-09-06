# 40 - Always-on watchdog (Phase 07). Runs forever, hidden, from logon.
#
#   .\scripts\40-watchdog.ps1            (normally started by the scheduled task)
#
# Keeps two things alive: the model stack (llama-swap + LiteLLM on :4000) and
# the face (:7777). Starts what is missing, restarts what has died, and once a
# week runs the session audit so the score trend accumulates unattended.
#
# ⚠ THREE RULES, each learned the hard way elsewhere in this project:
#   1. Never restart a face that is mid-conversation. /status reports `state`
#      and `gate_open`; anything but idle-and-closed is left alone. A watchdog
#      that kills Vajren while Mudit is answering a question is worse than none.
#   2. Require THREE consecutive failed checks (~90 s) before restarting the
#      face. A single timeout is a busy machine, not a dead process - the
#      cold first plan after a swap alone is ~84 s.
#   3. Never touch the stack while the face is up and busy - a model swap
#      mid-request is the J-032 failure mode (19-36 s of silence).
$ErrorActionPreference = "Continue"
$root = "C:\vajren"
$log  = "$root\logs\watchdog.log"
$py   = "$root\.venv\Scripts\python.exe"
Set-Location $root

function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Add-Content -Path $log -Encoding utf8 }
function Up($url, $t=4) { try { Invoke-RestMethod $url -TimeoutSec $t } catch { $null } }

Log "watchdog start (pid $PID)"
$faceFails = 0
$lastAudit = (Get-Date).AddDays(-8)

while ($true) {
  try {
    # ---- 1. stack -----------------------------------------------------------
    $gw = Up "http://127.0.0.1:4000/v1/models"
    if (-not $gw) {
      $face = Up "http://127.0.0.1:7777/status"
      if ($face -and $face.state -ne "idle") {
        Log "gateway down but face busy ($($face.state)) - waiting"
      } else {
        Log "gateway DOWN - starting stack"
        & "$root\scripts\04-start-stack.ps1" 2>&1 | ForEach-Object { Log "  stack: $_" }
      }
    }

    # ---- 2. face ------------------------------------------------------------
    $face = Up "http://127.0.0.1:7777/status"
    if ($face) {
      $faceFails = 0
    } else {
      $faceFails++
      Log "face check failed ($faceFails/3)"
      if ($faceFails -ge 3) {
        Log "face DOWN x3 - restarting via 20-ui"
        & "$root\scripts\20-ui.ps1" 2>&1 | ForEach-Object { Log "  ui: $_" }
        $faceFails = 0
      }
    }

    # ---- 2b. Telegram remote, only once .env has real keys ---------------------
    # core.remote exits 2 if the keys are missing/blank; we probe that quietly
    # every loop so filling in .env is all it takes - no restart, no re-install.
    if ($face) {
      $probe = & $py -X utf8 -c "import sys; sys.path.insert(0,'.'); from core.remote import _env; _env()" 2>$null; $keysOk = ($LASTEXITCODE -eq 0)
      if ($keysOk) {
        $rem = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "core\.remote" }
        if (-not $rem) {
          Log "telegram keys present, remote not running - starting core.remote"
          Start-Process -FilePath $py -ArgumentList @("-X","utf8","-m","core.remote") -WorkingDirectory $root `
            -RedirectStandardOutput "$root\logs\remote.log" -RedirectStandardError "$root\logs\remote.err.log" -WindowStyle Hidden
        }
      }
    }

    # ---- 3. weekly audit, only when idle ------------------------------------
    if (((Get-Date) - $lastAudit).TotalDays -ge 7 -and $face -and $face.state -eq "idle" -and -not $face.gate_open) {
      Log "weekly audit"
      $out = & $py -X utf8 "$root\scripts\31-session-audit.py" 20 2>&1 | Select-String -Pattern "mean score" | ForEach-Object { $_.Line.Trim() }
      "$(Get-Date -Format 'yyyy-MM-dd')  $out" | Add-Content -Path "$root\logs\audit-trend.txt" -Encoding utf8
      Log "  $out"
      $lastAudit = Get-Date
    }
  } catch {
    Log "loop error: $_"
  }
  Start-Sleep -Seconds 30
}
