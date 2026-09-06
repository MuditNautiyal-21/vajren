# 41 - Register Vajren to start at logon and stay alive (Phase 07).
#
#   .\scripts\41-install-always-on.ps1              register + start now
#   .\scripts\41-install-always-on.ps1 -Uninstall   remove
#   .\scripts\41-install-always-on.ps1 -Status      is it registered / running?
#
# A per-user Scheduled Task, NOT a Windows service: no admin needed, it runs in
# Mudit's own session (so it can see his desktop, mic and windows - a service
# in session 0 cannot), and it survives reboots. NSSM/service is the upgrade
# path once the restricted account from Phase 00 exists.
param([switch]$Uninstall, [switch]$Status)
$ErrorActionPreference = "Stop"
$name = "Vajren"
$root = "C:\vajren"

if ($Status) {
  $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if (-not $t) { Write-Host "  not registered" -ForegroundColor Yellow; exit 0 }
  $i = Get-ScheduledTaskInfo -TaskName $name
  Write-Host ("  registered  state={0}  lastRun={1}  lastResult={2}" -f $t.State, $i.LastRunTime, $i.LastTaskResult)
  $wd = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match "40-watchdog" }
  Write-Host ("  watchdog    {0}" -f $(if ($wd) { "running (pid $($wd.ProcessId))" } else { "NOT running" }))
  if (Test-Path "$root\logs\watchdog.log") { Get-Content "$root\logs\watchdog.log" -Tail 3 | ForEach-Object { "    $_" } }
  exit 0
}

if ($Uninstall) {
  Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match "40-watchdog" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "  stopped watchdog pid $($_.ProcessId)" }
  Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "  '$name' removed" -ForegroundColor Green
  exit 0
}

$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
             -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\scripts\40-watchdog.ps1`"" `
             -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# ⚠ Defaults kill long-running tasks: a 3-day execution limit and "stop if on
#   battery". Both off. RestartCount lets the task itself come back if the
#   watchdog process ever dies.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
              -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
              -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
  -Description "VAJREN always-on watchdog: keeps the model stack and the face alive; weekly self-audit." -Force | Out-Null
Write-Host "  '$name' registered: at logon, hidden, no time limit" -ForegroundColor Green

# Start it now rather than waiting for the next logon.
$running = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match "40-watchdog" }
if (-not $running) { Start-ScheduledTask -TaskName $name; Start-Sleep -Seconds 3; Write-Host "  started now" }
& $PSCommandPath -Status
