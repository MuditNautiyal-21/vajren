# 04 - Bring the whole local stack up.
#
# TWO MODES, picked automatically by whether bin\llama-swap.exe exists.
#
#   SWAP   (preferred)  llama-swap :8080 loads the right llama-server on demand
#                       and unloads the last. REQUIRED at 3+ models: the card
#                       holds one ~20 GB model at a time.
#   DIRECT (fallback)   one llama-server per model on a fixed port, all resident.
#                       Fine for 1-2 models, impossible for 3.
#
# The LiteLLM config MUST match the mode. litellm.yaml points at :8080;
# litellm-direct.yaml points at the fixed ports. Get it wrong and the gateway
# starts clean, lists every route, and 500s everything with "Connection error".
#
# ⚠ Every llama.cpp flag below is measured on this machine. See config\llama-swap.yaml
#   for why --device Vulkan0, --parallel 1 and --load-mode none are not optional.
$ErrorActionPreference = "Continue"
$root = "C:\vajren"
New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null

$swapExe = "$root\bin\llama-swap.exe"
$useSwap = Test-Path $swapExe

# ---------------------------------------------------------------- models ----
if ($useSwap) {
  Write-Host "  mode: SWAP (llama-swap :8080)" -ForegroundColor Cyan
  Get-Process llama-swap -EA SilentlyContinue | Stop-Process -Force
  Start-Process -FilePath $swapExe `
    -ArgumentList @("--config", "$root\config\llama-swap.yaml", "--listen", "127.0.0.1:8080") `
    -RedirectStandardOutput "$root\logs\llama-swap.log" `
    -RedirectStandardError  "$root\logs\llama-swap.err.log" -WindowStyle Hidden
  Start-Sleep -Seconds 3
  try {
    $m = Invoke-RestMethod "http://127.0.0.1:8080/v1/models" -TimeoutSec 10
    Write-Host "  llama-swap :8080  UP" -ForegroundColor Green
    foreach ($x in $m.data) { Write-Host ("      " + $x.id) }
    Write-Host "  (models load on first request, not now)" -ForegroundColor DarkGray
  } catch {
    Write-Host "  llama-swap :8080  DOWN - see logs\llama-swap.err.log" -ForegroundColor Red
  }
}
else {
  Write-Host "  mode: DIRECT (per-model ports)" -ForegroundColor Yellow
  Write-Host "  install llama-swap before adding a 3rd model: .\scripts\11-get-llama-swap.ps1" -ForegroundColor DarkGray
  $hw     = Get-Content "$root\config\hardware.json" -Raw | ConvertFrom-Json
  $device = if ($hw.backend -eq "vulkan") { @("--device", "Vulkan0") } else { @() }
  $cpu    = @("--device", "none", "-ngl", "0")

  $plan = @(
    @{ match = "*Qwen3-4B*";     alias = "reflex";    port = 8082; onGpu = $false;
       extra = @("--ctx-size","8192") },
    @{ match = "Qwen3.6-35B*";   alias = "workhorse"; port = 8081; onGpu = $true;
       extra = @("-ngl","999","--n-cpu-moe","26","--ctx-size","32768","--load-mode","none") }
  )

  $started = @(); $t0 = Get-Date
  foreach ($p in $plan) {
    $f = Get-ChildItem "$root\models" -Recurse -Filter "$($p.match).gguf" -EA SilentlyContinue | Select-Object -First 1
    if (-not $f) { Write-Host ("  skip  {0,-10} (no weights yet)" -f $p.alias) -ForegroundColor DarkGray; continue }
    Write-Host ("  start {0,-10} :{1}  {2}" -f $p.alias, $p.port, $f.Name) -ForegroundColor Cyan
    $args = @("-m", $f.FullName, "--alias", $p.alias, "--host", "127.0.0.1", "--port", $p.port,
              "--flash-attn", "on", "--jinja", "--no-warmup", "--parallel", "1",
              "--cache-type-k", "q8_0", "--cache-type-v", "q8_0") +
            $(if ($p.onGpu) { $device } else { $cpu }) + $p.extra
    Start-Process -FilePath "$root\llama\llama-server.exe" -ArgumentList $args `
      -RedirectStandardOutput "$root\logs\$($p.alias).log" `
      -RedirectStandardError  "$root\logs\$($p.alias).err.log" -WindowStyle Hidden
    $started += $p
  }
  if (-not $started) { Write-Host "`nNo weights found." -ForegroundColor Yellow; exit 1 }

  Write-Host "`n  waiting for models to load (up to 5 min)..." -ForegroundColor Cyan
  $deadline = (Get-Date).AddMinutes(5)
  $pending  = [System.Collections.ArrayList]::new($started)
  while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    foreach ($p in @($pending)) {
      try {
        Invoke-RestMethod "http://127.0.0.1:$($p.port)/health" -TimeoutSec 3 | Out-Null
        Write-Host ("  {0,-10} :{1}  UP   ({2}s)" -f $p.alias, $p.port, [int]((Get-Date) - $t0).TotalSeconds) -ForegroundColor Green
        $pending.Remove($p)
      } catch { }
    }
  }
  foreach ($p in $pending) {
    Write-Host ("  {0,-10} :{1}  DOWN - see logs\{0}.err.log" -f $p.alias, $p.port) -ForegroundColor Red
  }
}

# --------------------------------------------------------------- gateway ----
Write-Host "`n  starting LiteLLM on :4000 ..." -ForegroundColor Cyan
# ⚠ NOT `python -m litellm` — LiteLLM ships no __main__.
$py      = (Get-Content "$root\config\python-path.txt").Trim()
$litellm = Join-Path (Split-Path $py -Parent) "litellm.exe"
if (-not (Test-Path $litellm)) {
  Write-Host "  litellm.exe not in the venv. Run: .venv\Scripts\pip.exe install `"litellm[proxy]`"" -ForegroundColor Red
  exit 1
}

# ⚠ PYTHONIOENCODING is load-bearing. LiteLLM prints an ASCII banner at startup;
#   on a cp1252 console that throws UnicodeEncodeError INSIDE the FastAPI startup
#   lifespan, so the proxy does not degrade - it exits, and there is no gateway.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8       = "1"

$cfg = if ($useSwap) { "$root\config\litellm.yaml" } else { "$root\config\litellm-direct.yaml" }
Write-Host ("  config: " + (Split-Path $cfg -Leaf)) -ForegroundColor DarkGray
Get-Process litellm -EA SilentlyContinue | Stop-Process -Force
Start-Process -FilePath $litellm -ArgumentList @("--config", $cfg, "--port", "4000") `
  -RedirectStandardOutput "$root\logs\litellm.log" `
  -RedirectStandardError  "$root\logs\litellm.err.log" -WindowStyle Hidden

Start-Sleep -Seconds 14
try {
  $m = Invoke-RestMethod "http://127.0.0.1:4000/v1/models" -TimeoutSec 8
  Write-Host "  litellm    :4000  UP" -ForegroundColor Green
  foreach ($x in $m.data) { Write-Host ("      " + $x.id) }
} catch {
  Write-Host "  litellm    :4000  DOWN - see logs\litellm.err.log" -ForegroundColor Red
  exit 1
}

# ------------------------------------------------------------------ warm ----
# In SWAP mode nothing is loaded until something asks, so the FIRST real request
# pays the load: measured 19 s cold, 36 s when another specialist has to be
# evicted first. Absorb that here, while the user is still reading the startup
# output, instead of making them sit through it mid-sentence.
# Only the workhorse — it backs the planner and every default lane.
if ($useSwap) {
  # ⚠ Warm with the prompt the planner ACTUALLY sends, not "hi".
  #
  #   "hi" loaded the weights — that was the ~20 s — and left the KV cache
  #   holding two tokens, so the first real request still prefilled the whole
  #   static head from scratch. Measured 2026-09-08: turn 1 was 8,893 prompt
  #   tokens with 0 cached, 79 s of prefill, 105.8 s total. Turn 2, same
  #   sentence, 7,953 cached: 15.3 s. He was paying 79 s to tell the model who
  #   it is, once per cold start.
  #
  #   Re-measured 2026-09-17 with the real head: 20.6 s cold, then 2.9–4.6 s
  #   for DIFFERENT requests sharing it. 46-warm.py builds the head from the
  #   same functions plan() uses rather than a copy, because prefix caching is
  #   exact-match and a drifted copy warms nothing while still looking busy.
  Write-Host "`n  warming the workhorse with the real prompt (~25s)..." -ForegroundColor Cyan
  $t0 = Get-Date
  & "$root\.venv\Scripts\python.exe" -X utf8 "$root\scripts\46-warm.py" private 2>&1 |
    ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
  if ($LASTEXITCODE -eq 0) {
    Write-Host ("  workhorse  warm ({0}s, prompt head cached)" -f [int]((Get-Date) - $t0).TotalSeconds) -ForegroundColor Green
  } else {
    Write-Host "  warm-up failed - it will load on first use instead" -ForegroundColor Yellow
  }
}

# ---------------------------------------------------------------------------
# The overlay. A separate process ON PURPOSE: it must never be able to take
# the assistant down, and a paint loop must never share a GIL with the planner.
# It shows itself only while the face is NOT the window he is looking at, so
# starting it here costs him nothing when the face is in front.
# ---------------------------------------------------------------------------
# ⚠ pythonw.exe, not python.exe — it is started windowless below, so looking
#   for python.exe here would never find it and would start a second one on
#   every run until there were a dozen orbs on screen.
$already = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -in @("python.exe", "pythonw.exe") -and $_.CommandLine -like "*core.overlay*" }
if ($already) {
  Write-Host "  overlay    already running (pid $($already.ProcessId))" -ForegroundColor DarkGray
} else {
  Start-Process -FilePath "$root\.venv\Scripts\pythonw.exe" `
                -ArgumentList @("-m", "core.overlay") `
                -WorkingDirectory $root -WindowStyle Hidden
  Write-Host "  overlay    up (appears when the face is behind something)" -ForegroundColor Green
}

Write-Host "`n  ready.  ask it something:" -ForegroundColor Green
Write-Host "    .venv\Scripts\python.exe scripts\ask.py `"your request here`"" -ForegroundColor White
