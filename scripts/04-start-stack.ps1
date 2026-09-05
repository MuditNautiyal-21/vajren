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
$cpu    = @("--device", "none", "-ngl", "0")

# model file -> alias, port, extra flags.
#
# ⚠ The numbers below are MEASURED on this machine (llama-bench, build b10796,
#   RX 6750 XT 12 GB). They are not defaults and not estimates. See J-029.
#
#   workhorse --n-cpu-moe :  20 -> hung the machine   23 -> untested
#                            26 -> 30.6 tok/s  ✅     29 -> 25.4   32 -> 20.7
#   Lower = more experts on the GPU = faster, until it overcommits and Windows
#   starts spilling to "Shared GPU memory", at which point it does not error,
#   it just crawls. 26 is the last value that still fits.
#
# ⚠ reflex runs on the CPU on purpose (--device none). On the GPU it is 6x
#   faster in isolation (122 vs 20 tok/s) - and completely not worth it: it is
#   2.6 GB resident, which pushes the workhorse from n-cpu-moe 26 to ~33 and
#   costs the model that does the actual thinking a third of its speed. Reflex
#   only routes and classifies short utterances; 122 tok/s of prompt processing
#   clears that in about a second. Revisit if the card ever gets bigger.
$plan = @(
  @{ match = "*Qwen3-4B*";      alias = "reflex";    port = 8082; onGpu = $false;
     extra = @("--ctx-size","8192") },
  # ⚠ --load-mode none on every model that uses --n-cpu-moe. llama.cpp warns
  #   about this itself: "tensor overrides to CPU are used with mmap enabled -
  #   consider using --load-mode none for better performance". With mmap the
  #   CPU-resident experts are page-faulted in during inference, which showed up
  #   as 11 tok/s on the first request and 22 on the second - against 30.6 in
  #   llama-bench. Slower to load, correct once loaded.
  @{ match = "Qwen3.6-35B*";    alias = "workhorse"; port = 8081; onGpu = $true;
     extra = @("-ngl","999","--n-cpu-moe","26","--ctx-size","32768","--load-mode","none") },
  @{ match = "GLM-4.7-Flash*";  alias = "tools";     port = 8083; onGpu = $true;
     extra = @("-ngl","999","--n-cpu-moe","22","--ctx-size","32768","--load-mode","none") },
  @{ match = "*gemma-4-31B*";   alias = "writer";    port = 8084; onGpu = $true;
     extra = @("-ngl","999","--ctx-size","16384") }
)

$started = @()
$t0 = Get-Date
foreach ($p in $plan) {
  $f = Get-ChildItem "$root\models" -Recurse -Filter "$($p.match).gguf" -EA SilentlyContinue | Select-Object -First 1
  if (-not $f) { Write-Host ("  skip  {0,-10} (no weights yet)" -f $p.alias) -ForegroundColor DarkGray; continue }

  Write-Host ("  start {0,-10} :{1}  {2}" -f $p.alias, $p.port, $f.Name) -ForegroundColor Cyan
  $args = @("-m", $f.FullName, "--alias", $p.alias,
            "--host", "127.0.0.1", "--port", $p.port,
            "--flash-attn", "on", "--jinja", "--no-warmup",
            # ⚠ --parallel 1. llama-server defaults to FOUR slots, and each slot
            #   gets its own full KV cache - so "--ctx-size 32768" quietly became
            #   131072 tokens of cache and ate the VRAM the experts needed.
            #   Vajren serves one person; it never needs four concurrent slots.
            "--parallel", "1",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0") +
          $(if ($p.onGpu) { $device } else { $cpu }) + $p.extra
  Start-Process -FilePath "$root\llama\llama-server.exe" -ArgumentList $args `
    -RedirectStandardOutput "$root\logs\$($p.alias).log" `
    -RedirectStandardError  "$root\logs\$($p.alias).err.log" -WindowStyle Hidden
  $started += $p
}

if (-not $started) { Write-Host "`nNo weights found. Run: .venv\Scripts\python.exe scripts\get_models.py" -ForegroundColor Yellow; exit 1 }

# Poll rather than sleep a fixed amount. A 22 GB model coming off NVMe cold can
# take well over a minute; a fixed 25s wait reported it DOWN while it was still
# perfectly healthily loading, which sends you log-diving for a non-problem.
Write-Host "`n  waiting for models to load (up to 5 min)..." -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(5)
$pending  = [System.Collections.ArrayList]::new($started)

while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 5
  foreach ($p in @($pending)) {
    try {
      Invoke-RestMethod "http://127.0.0.1:$($p.port)/health" -TimeoutSec 3 | Out-Null
      $secs = [int]((Get-Date) - $t0).TotalSeconds
      Write-Host ("  {0,-10} :{1}  UP   ({2}s)" -f $p.alias, $p.port, $secs) -ForegroundColor Green
      $pending.Remove($p)
    } catch { }
  }
}

foreach ($p in $pending) {
  Write-Host ("  {0,-10} :{1}  DOWN - see logs\{0}.err.log" -f $p.alias, $p.port) -ForegroundColor Red
}

Write-Host "`n  starting LiteLLM on :4000 ..." -ForegroundColor Cyan
# ⚠ NOT `python -m litellm`. LiteLLM ships no __main__, so that fails with
#   "'litellm' is a package and cannot be directly executed". The proxy is the
#   console script next to python.exe in the same venv.
$py       = (Get-Content "$root\config\python-path.txt").Trim()
$litellm  = Join-Path (Split-Path $py -Parent) "litellm.exe"
if (-not (Test-Path $litellm)) {
  Write-Host "  litellm.exe not found in the venv. Run: .venv\Scripts\pip.exe install `"litellm[proxy]`"" -ForegroundColor Red
} else {
  # ⚠ PYTHONIOENCODING is load-bearing, not cosmetic.
  #   LiteLLM prints an ASCII-art banner at startup. On a stock Windows console
  #   (cp1252) that throws UnicodeEncodeError *inside the startup lifespan*, so
  #   the proxy does not degrade - it exits, and the whole stack has no gateway.
  #   Diagnosing this costs half an hour, because the traceback is ~200 lines of
  #   FastAPI lifespan nesting and the actual cause is the last line: a logo.
  $env:PYTHONIOENCODING = "utf-8"
  $env:PYTHONUTF8       = "1"

  # Pick the config that matches what is actually listening. litellm.yaml points
  # at llama-swap :8080; this script does not start llama-swap. Get that wrong
  # and the gateway comes up healthy, lists all twelve routes, and 500s every
  # request with "Connection error".
  $swap = Get-Process llama-swap -EA SilentlyContinue
  $cfg  = if ($swap) { "$root\config\litellm.yaml" } else { "$root\config\litellm-direct.yaml" }
  Write-Host ("  mode: " + $(if ($swap) { "SWAP (llama-swap :8080)" } else { "DIRECT (per-model ports)" }))

  Start-Process -FilePath $litellm -ArgumentList @("--config",$cfg,"--port","4000") `
    -RedirectStandardOutput "$root\logs\litellm.log" -RedirectStandardError "$root\logs\litellm.err.log" -WindowStyle Hidden
}
Start-Sleep -Seconds 12
try {
  $m = Invoke-RestMethod "http://127.0.0.1:4000/v1/models" -TimeoutSec 8
  Write-Host "  litellm    :4000  UP" -ForegroundColor Green
  $m.data | ForEach-Object { Write-Host ("      " + $_.id) }
} catch {
  Write-Host "  litellm    :4000  DOWN - see logs\litellm.err.log" -ForegroundColor Red
}
