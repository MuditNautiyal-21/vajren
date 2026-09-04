# 04 - Bring the stack up.
#
# Starts a llama-server for each model actually present in models\, then LiteLLM
# in front of them. Deliberately does NOT require llama-swap: with one model
# there is nothing to rotate, and adding a swap proxy before the plumbing is
# proven just gives failures two places to hide. llama-swap comes in with tier 2.
#
# ⚠ --device Vulkan0. On this machine --list-devices shows two Vulkan devices,
# and the SECOND one is the integrated GPU advertising 16 GB of shared system
# RAM - more than the real card. Left to choose, llama.cpp can land on the iGPU
# and crawl while still looking like it is "using a GPU". Verify the index with
# `llama\llama-server.exe --list-devices` on any new machine.
$ErrorActionPreference = "Continue"
$root = "C:\vajren"
New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null

$hw     = Get-Content "$root\config\hardware.json" -Raw | ConvertFrom-Json
$device = if ($hw.backend -eq "vulkan") { @("--device", "Vulkan0") } else { @() }

# model file -> alias, port, extra flags
$plan = @(
  @{ match = "Qwen3-4B*";       alias = "reflex";    port = 8082; extra = @("-ngl","999","--ctx-size","8192") },
  @{ match = "Qwen3.6-35B*";    alias = "workhorse"; port = 8081; extra = @("-ngl","999","--n-cpu-moe","12","--ctx-size","32768") },
  @{ match = "GLM-4.7-Flash*";  alias = "tools";     port = 8083; extra = @("-ngl","999","--n-cpu-moe","10","--ctx-size","32768") },
  @{ match = "gemma-4-31B*";    alias = "writer";    port = 8084; extra = @("-ngl","999","--ctx-size","16384") }
)

$started = @()
foreach ($p in $plan) {
  $f = Get-ChildItem "$root\models" -Recurse -Filter "$($p.match).gguf" -EA SilentlyContinue | Select-Object -First 1
  if (-not $f) { Write-Host ("  skip  {0,-10} (no weights yet)" -f $p.alias) -ForegroundColor DarkGray; continue }

  Write-Host ("  start {0,-10} :{1}  {2}" -f $p.alias, $p.port, $f.Name) -ForegroundColor Cyan
  $args = @("-m", $f.FullName, "--alias", $p.alias,
            "--host", "127.0.0.1", "--port", $p.port,
            "--flash-attn", "--jinja", "--no-warmup",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0") + $device + $p.extra
  Start-Process -FilePath "$root\llama\llama-server.exe" -ArgumentList $args `
    -RedirectStandardOutput "$root\logs\$($p.alias).log" `
    -RedirectStandardError  "$root\logs\$($p.alias).err.log" -WindowStyle Hidden
  $started += $p
}

if (-not $started) { Write-Host "`nNo weights found. Run: .venv\Scripts\python.exe scripts\get_models.py" -ForegroundColor Yellow; exit 1 }

Write-Host "`n  waiting for models to load..." -ForegroundColor Cyan
Start-Sleep -Seconds 25

foreach ($p in $started) {
  try {
    $r = Invoke-RestMethod "http://127.0.0.1:$($p.port)/health" -TimeoutSec 5
    Write-Host ("  {0,-10} :{1}  UP" -f $p.alias, $p.port) -ForegroundColor Green
  } catch {
    Write-Host ("  {0,-10} :{1}  DOWN - see logs\{0}.err.log" -f $p.alias, $p.port) -ForegroundColor Red
  }
}

Write-Host "`n  starting LiteLLM on :4000 ..." -ForegroundColor Cyan
$py = (Get-Content "$root\config\python-path.txt").Trim()
Start-Process -FilePath $py -ArgumentList @("-m","litellm","--config","$root\config\litellm.yaml","--port","4000") `
  -RedirectStandardOutput "$root\logs\litellm.log" -RedirectStandardError "$root\logs\litellm.err.log" -WindowStyle Hidden
Start-Sleep -Seconds 12
try {
  $m = Invoke-RestMethod "http://127.0.0.1:4000/v1/models" -TimeoutSec 8
  Write-Host "  litellm    :4000  UP" -ForegroundColor Green
  $m.data | ForEach-Object { Write-Host ("      " + $_.id) }
} catch {
  Write-Host "  litellm    :4000  DOWN - see logs\litellm.err.log" -ForegroundColor Red
}
