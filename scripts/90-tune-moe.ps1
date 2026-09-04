# 90 - Find the right --n-cpu-moe value for the workhorse model.
#
# THE RULE: start low, raise until it JUST fits. Going past "just fits" makes it
# slower, not faster - you are adding PCIe traffic for nothing. On a 12 GB card a
# correctly-tuned MoE offload measured 11 tok/s -> 54 tok/s purely from not
# overcommitting VRAM.
#
# Watch actual VRAM in Task Manager > Performance > GPU while this runs.
# If "Shared GPU memory" starts climbing, you have overcommitted: raise the value.

$root  = "F:\Programs\AI\VAJREN"
$model = "$root\models\Qwen3.6-35B-A3B-Q4_K_M.gguf"

foreach ($n in 8,10,12,14,16,20) {
  Write-Host "`n=== --n-cpu-moe $n ===" -ForegroundColor Cyan
  & "$root\llama\llama-bench.exe" -m $model -ngl 999 --n-cpu-moe $n -p 512 -n 128 -r 2
}

Write-Host "`nPick the LOWEST value where tg (token generation) is at its peak" -ForegroundColor Green
Write-Host "and shared GPU memory stays flat. Write it into config\llama\workhorse.args."
Write-Host "`nDO NOT enable speculative decoding on Vulkan - a known single-queue bug" -ForegroundColor Yellow
Write-Host "collapses throughput from 33 tok/s to 0.014 tok/s. llama.cpp #23126.`n"
