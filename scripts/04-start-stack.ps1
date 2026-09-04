# 04 - Bring the stack up by hand. (Phase 07 replaces this with NSSM services.)
$ErrorActionPreference = "Continue"
$root = "C:\Users\ytdek\vajren"

Write-Host "Starting workhorse on :8081 ..." -ForegroundColor Cyan
Start-Process -FilePath "$root\llama\llama-server.exe" `
  -ArgumentList "--args-file","$root\config\llama\workhorse.args" `
  -RedirectStandardOutput "$root\logs\workhorse.log" `
  -RedirectStandardError  "$root\logs\workhorse.err.log" `
  -WindowStyle Hidden

Start-Sleep -Seconds 20

Write-Host "Starting reflex on :8082 ..." -ForegroundColor Cyan
Start-Process -FilePath "$root\llama\llama-server.exe" `
  -ArgumentList "--args-file","$root\config\llama\reflex.args" `
  -RedirectStandardOutput "$root\logs\reflex.log" `
  -RedirectStandardError  "$root\logs\reflex.err.log" `
  -WindowStyle Hidden

Start-Sleep -Seconds 10

Write-Host "Starting LiteLLM router on :4000 ..." -ForegroundColor Cyan
Start-Process -FilePath "conda" `
  -ArgumentList "run","-n","vajren","litellm","--config","$root\config\litellm.yaml","--port","4000" `
  -RedirectStandardOutput "$root\logs\litellm.log" `
  -RedirectStandardError  "$root\logs\litellm.err.log" `
  -WindowStyle Hidden

Start-Sleep -Seconds 8
Write-Host "`nHealth check:" -ForegroundColor Cyan
foreach ($p in 8081,8082,4000) {
  try {
    $null = Invoke-WebRequest "http://127.0.0.1:$p/health" -TimeoutSec 5 -UseBasicParsing
    Write-Host ("  :{0}  UP" -f $p) -ForegroundColor Green
  } catch {
    Write-Host ("  :{0}  DOWN - check logs\" -f $p) -ForegroundColor Red
  }
}
Write-Host "`nSmoke test:  curl http://127.0.0.1:4000/v1/models`n"
