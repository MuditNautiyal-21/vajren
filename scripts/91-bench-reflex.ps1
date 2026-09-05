# 91 - Can the reflex model live on the CPU?
#
# WHY THIS QUESTION EXISTS: llama-swap pins reflex as a resident model so every
# request can hit it first. Resident on the GPU costs ~3 GB of the 12 GB card,
# and that 3 GB comes straight out of the workhorse's expert budget - measured,
# it is the difference between ~30 tok/s and ~20 tok/s on the model that does
# the actual thinking. A router that is "fast" is worth nothing if it made the
# brain 33% slower.
#
# Reflex only routes and classifies. It does not need to be quick in absolute
# terms, it needs to be quick relative to a human turn. If CPU-only clears
# ~12 tok/s the trade is obviously worth it.

$ErrorActionPreference = "Continue"
$root  = "C:\vajren"
$model = "$root\models\Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

if (-not (Test-Path $model)) { Write-Host "missing: $model" -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null

Write-Host "`n--- reflex on CPU (--device none) ---" -ForegroundColor Cyan
& "$root\llama\llama-bench.exe" -m $model --device none -ngl 0 `
    -p 512 -n 128 -r 2 2>&1 | Tee-Object -FilePath "$root\logs\bench-reflex-cpu.log"

Write-Host "`n--- reflex on GPU (Vulkan0) for comparison ---" -ForegroundColor Cyan
& "$root\llama\llama-bench.exe" -m $model --device Vulkan0 -ngl 999 `
    -p 512 -n 128 -r 2 2>&1 | Tee-Object -FilePath "$root\logs\bench-reflex-gpu.log"
