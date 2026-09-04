# 02 - llama.cpp with the VULKAN backend.
#
# WHY VULKAN AND NOT ROCm:
#   AMD's HIP SDK requires Windows 11 and lists gfx1031 (RX 6750 XT) as
#   unsupported even there. ROCm on WSL2 also dropped RDNA2. Every guide telling
#   you to set HSA_OVERRIDE_GFX_VERSION is describing a community patch that
#   breaks on the next update. Vulkan needs nothing but your Adrenalin driver and
#   benchmarks at ~82 tok/s generation on this exact card.
$ErrorActionPreference = "Stop"
$root = "F:\Programs\AI\VAJREN"
$dest = "$root\llama"

Write-Host "Fetching latest llama.cpp Vulkan release for Windows..." -ForegroundColor Cyan
$rel = Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
$asset = $rel.assets | Where-Object { $_.name -like "*bin-win-vulkan-x64.zip" } | Select-Object -First 1

if (-not $asset) {
  Write-Host "Could not find a vulkan-x64 asset in release $($rel.tag_name)." -ForegroundColor Red
  Write-Host "Open https://github.com/ggml-org/llama.cpp/releases and grab it manually." -ForegroundColor Yellow
  exit 1
}

Write-Host ("  {0}  ({1})" -f $asset.name, $rel.tag_name)
$zip = "$env:TEMP\$($asset.name)"
Invoke-WebRequest $asset.browser_download_url -OutFile $zip
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Expand-Archive -Path $zip -DestinationPath $dest -Force
Remove-Item $zip

Write-Host "`nVerifying the GPU is visible to the Vulkan backend..." -ForegroundColor Cyan
& "$dest\llama-server.exe" --list-devices

Write-Host "`nExpect to see your RX 6750 XT listed above." -ForegroundColor Green
Write-Host "Pinned release: $($rel.tag_name) - record it in docs/DECISIONS.md`n"
