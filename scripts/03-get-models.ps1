# 03 - Download the bench. ~80 GB total if you take everything.
#
# Models live on the INTERNAL NVMe (disk 0 / C:), NOT on F:.
# F: is a Samsung T7 Shield over USB - fine for archives, wrong for a hot model
# cache that a 24/7 service reads 20 GB at a time from. See docs/DECISIONS.md D-014.
$ErrorActionPreference = "Stop"
$models = "C:\vajren\models"
New-Item -ItemType Directory -Force -Path $models | Out-Null

conda run -n vajren python -m pip install -U "huggingface_hub[cli]"

function Get-Gguf($repo, $pattern, $label, $gb) {
  Write-Host "`n  $label  (~$gb GB)  <- $repo" -ForegroundColor Cyan
  conda run -n vajren huggingface-cli download $repo --include $pattern --local-dir $models
}

Write-Host "`n=== TIER 1: start here. Enough to run the loop. ===" -ForegroundColor Green
Get-Gguf "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF" "*Q4_K_M*.gguf"  "reflex    - pinned classifier, always resident" 2.5
Get-Gguf "unsloth/Qwen3.6-35B-A3B-GGUF"                "*Q4_K_M*.gguf"  "workhorse - coding + planning"                  22.1

Write-Host "`n=== TIER 2: the specialists. Add once tier 1 works. ===" -ForegroundColor Green
Get-Gguf "unsloth/GLM-4.7-Flash-GGUF"                  "*Q4_K_XL*.gguf" "tools     - function calling, tau2-bench 79.5"  17.5
Get-Gguf "Qwen/Qwen3-VL-8B-Instruct-GGUF"              "*.gguf"         "vision    - screenshots, charts (+ mmproj)"      6.0

Write-Host "`n=== TIER 3: the writing A/B. Pick one, delete the other. ===" -ForegroundColor Green
Write-Host "  Run tests\writing-bench on YOUR real emails before choosing." -ForegroundColor Yellow
Get-Gguf "bartowski/google_gemma-4-31B-it-GGUF"        "*Q4_K_M*.gguf"  "writer A  - Creative Writing v3: 1407.6 (dense)" 18.5
# writer B is GLM-4.7-Flash, already downloaded above as the tools lane (1400.8, MoE)

Write-Host "`n=== Embeddings (CPU) ===" -ForegroundColor Green
Get-Gguf "Qwen/Qwen3-Embedding-0.6B-GGUF"              "*Q8_0*.gguf"    "embeddings - memory + file search"               0.7

Write-Host "`n--- on disk ---" -ForegroundColor Cyan
Get-ChildItem $models -Filter *.gguf -Recurse | ForEach-Object {
  Write-Host ("  {0,-52} {1,6} GB" -f $_.Name, [math]::Round($_.Length/1GB,1))
}
Write-Host "`nIf a filename differs from config\llama-swap.yaml, fix the yaml.`n" -ForegroundColor Yellow
