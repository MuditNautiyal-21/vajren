# 05 - Restart just the LiteLLM gateway, leaving the loaded models alone.
#
# Exists because the models take a minute to load and the gateway takes two
# seconds. When you are iterating on config\litellm.yaml you do not want to
# evict 22 GB of weights to test a YAML change.
$ErrorActionPreference = "Continue"
$root = "C:\vajren"

Get-Process litellm -EA SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

$py      = (Get-Content "$root\config\python-path.txt").Trim()
$litellm = Join-Path (Split-Path $py -Parent) "litellm.exe"

# Pick the config that matches what is actually listening. Pointing the gateway
# at llama-swap when llama-swap is not running gives you a proxy that starts
# clean, lists every route, and then 500s on every request with "Connection
# error" - see the header of config/litellm-direct.yaml.
$swap = Get-Process llama-swap -EA SilentlyContinue
$cfg  = if ($swap) { "$root\config\litellm.yaml" } else { "$root\config\litellm-direct.yaml" }
Write-Host ("  mode: " + $(if ($swap) { "SWAP (llama-swap :8080)" } else { "DIRECT (per-model ports)" }))

# See 04-start-stack.ps1 - the banner kills the proxy on a cp1252 console.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8       = "1"

Start-Process -FilePath $litellm -ArgumentList @("--config",$cfg,"--port","4000") `
  -RedirectStandardOutput "$root\logs\litellm.log" -RedirectStandardError "$root\logs\litellm.err.log" -WindowStyle Hidden

Start-Sleep -Seconds 14
try {
  $m = Invoke-RestMethod "http://127.0.0.1:4000/v1/models" -TimeoutSec 8
  Write-Host "  litellm    :4000  UP" -ForegroundColor Green
  foreach ($x in $m.data) { Write-Host ("      " + $x.id) }
} catch {
  Write-Host "  litellm    :4000  DOWN - see logs\litellm.err.log" -ForegroundColor Red
}
