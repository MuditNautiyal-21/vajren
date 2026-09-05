param([switch] $Bench, [switch] $Tools)

# ⚠ STOP THE STACK FIRST: .\scripts\00-stop-stack.ps1
#   llama-bench allocates the card for itself. With llama-swap holding a model
#   at the same time they fight over 12 GB, and the loser is whichever one you
#   were not watching. Every number below is invalid if the stack is up.

# ⚠ The sweep range is hardcoded, not a parameter, on purpose.
# Array arguments do not survive being passed in from a remote shell - a
# "-Sweep 26,29,32" arrived here once as the single integer 262932 and the
# bench dutifully started measuring a nonsense offload. Edit the line below.
$Sweep = if ($Tools) { @(14, 18, 22) } else { @(26, 29, 32) }

# 90 - Find the right MoE offload for the workhorse model.
#
# THE RULE: keep as much on the GPU as fits, and not one byte more. Overcommitting
# does not fail loudly - Windows silently spills into "Shared GPU memory" (system
# RAM over PCIe) and you get a model that loads fine and runs at walking pace.
# That is why the target here is measured, not guessed.
#
# The previous version of this script swept 8..20 and had no --device flag.
# Both were wrong. 8..20 is the wrong neighbourhood entirely for a 22.1 GB model
# on a 12 GB card - the experts are ~90% of an A3B model's weight, so most of
# them have to live in RAM. And with no --device, llama.cpp can pick Vulkan1,
# the integrated GPU, which advertises 16 GB of shared system RAM - MORE than
# the real card - making every number it prints meaningless.
#
# NEVER add speculative decoding on Vulkan: a single-queue bug collapses
# throughput from 33 tok/s to 0.014 tok/s. llama.cpp #23126.
#
# PHASE 1 (no args)  ask llama.cpp itself. This build has --fit (on by default),
#                    which sizes the offload to the device; -fitp prints what it
#                    chose. One model load. Start here, not with a blind sweep.
# PHASE 2 (-Sweep)   bench a narrow range around that answer to confirm the peak.

$ErrorActionPreference = "Continue"
$root  = "C:\vajren"
$model = if ($Tools) { "$root\models\GLM-4.7-Flash-UD-Q4_K_XL.gguf" }
         else        { "$root\models\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" }
$ctx   = 32768
$tag   = if ($Tools) { "tools" } else { "workhorse" }

if (-not (Test-Path $model)) { Write-Host "missing: $model" -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null

if (-not $Bench) {
  Write-Host "`n=== PHASE 1: what does llama.cpp fit on its own? ===" -ForegroundColor Cyan
  Write-Host ("model  " + (Split-Path $model -Leaf))
  Write-Host "ctx    $ctx     device  Vulkan0`n"

  # Deliberately do NOT pass -ngl or --n-cpu-moe. Setting them switches off the
  # very autosizing we are trying to read.
  & "$root\llama\llama-fit-params.exe" `
      -m $model --device Vulkan0 --ctx-size $ctx `
      --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 `
      --fit on --fit-print on 2>&1 | Tee-Object -FilePath "$root\logs\fit-workhorse.log"

  Write-Host "`nLook for the line naming n_cpu_moe / CPU-resident layers." -ForegroundColor Green
  Write-Host "Then confirm the peak around it:"
  Write-Host "  .\scripts\90-tune-moe.ps1 -Bench`n" -ForegroundColor White
  exit 0
}

Write-Host "`n=== PHASE 2: bench sweep $($Sweep -join ', ') ===" -ForegroundColor Cyan
foreach ($n in $Sweep) {
  Write-Host "`n--- --n-cpu-moe $n ---" -ForegroundColor Cyan
  & "$root\llama\llama-bench.exe" -m $model --device Vulkan0 `
      -ngl 999 --n-cpu-moe $n -fa 1 -ctk q8_0 -ctv q8_0 -p 512 -n 128 -r 2 2>&1 |
    Tee-Object -FilePath "$root\logs\bench-$tag-moe-$n.log"
}

Write-Host "`nPick the LOWEST value where tg (token generation) is at its peak," -ForegroundColor Green
Write-Host "and Task Manager > Performance > GPU > 'Shared GPU memory' stays flat."
Write-Host "Write it into config\llama-swap.yaml AND scripts\04-start-stack.ps1 - both.`n"
