# 03 - Model weights, staged.
#
#   .\scripts\03-get-models.ps1            # tier 1 only: reflex, ~2.5 GB
#   .\scripts\03-get-models.ps1 -All       # the whole bench, ~43 GB
#
# STAGED ON PURPOSE. Do not download 43 GB to find out the plumbing is broken.
# The reflex model alone proves llama.cpp runs on this GPU, the server starts,
# llama-swap rotates, LiteLLM routes and the graph executes a tool. It is too
# small to plan well - but perfect for proving the stack is sound. Then fetch
# the rest knowing it works.
param([switch]$All)

$ErrorActionPreference = "Continue"
$root   = "C:\vajren"
$models = "$root\models"
$py     = (Get-Content "$root\config\python-path.txt").Trim()
New-Item -ItemType Directory -Force -Path $models | Out-Null

# hf transfer is much faster on big files and resumes cleanly
$env:HF_HUB_ENABLE_HF_TRANSFER = "1"
& $py -m pip install -q hf_transfer 2>&1 | Out-Null

function Get-Gguf($repo, $pattern, $label, $gb) {
  Write-Host "`n  $label  (~$gb GB)" -ForegroundColor Cyan
  Write-Host "    $repo  [$pattern]"
  & $py -m huggingface_hub.commands.huggingface_cli download $repo `
      --include $pattern --local-dir $models
}

Write-Host "=== TIER 1 - enough to prove the stack ===" -ForegroundColor Green
Get-Gguf "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF" "*Q4_K_M.gguf" "reflex - pinned classifier" 2.5

if ($All) {
  Write-Host "`n=== TIER 2 - the workhorse ===" -ForegroundColor Green
  Get-Gguf "unsloth/Qwen3.6-35B-A3B-GGUF" "*Q4_K_M*.gguf" "coder + planner" 22.1

  Write-Host "`n=== TIER 3 - the specialists ===" -ForegroundColor Green
  Get-Gguf "unsloth/GLM-4.7-Flash-GGUF"   "*Q4_K_XL*.gguf" "tools - function calling" 17.5
  Get-Gguf "Qwen/Qwen3-VL-8B-Instruct-GGUF" "*.gguf"       "vision (+ mmproj)"        6.0
  Get-Gguf "bartowski/google_gemma-4-31B-it-GGUF" "*Q4_K_M*.gguf" "writer"             18.5
} else {
  Write-Host "`n  Tier 1 only. Run with -All for the full bench (~43 GB)." -ForegroundColor Yellow
}

Write-Host "`n--- on disk ---" -ForegroundColor Cyan
Get-ChildItem $models -Filter *.gguf -Recurse | ForEach-Object {
  Write-Host ("  {0,-56} {1,6:N1} GB" -f $_.Name, ($_.Length / 1GB))
}
