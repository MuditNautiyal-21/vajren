# 00 - Stop everything Vajren has running. Safe to run when nothing is up.
$ErrorActionPreference = "SilentlyContinue"
foreach ($n in "llama-server", "llama-swap", "litellm") {
  $procs = Get-Process $n -EA SilentlyContinue
  if ($procs) {
    Write-Host ("  stopping {0,-14} ({1})" -f $n, $procs.Count) -ForegroundColor Yellow
    $procs | Stop-Process -Force
  }
}
Start-Sleep -Seconds 2
Write-Host "  stopped." -ForegroundColor Green
