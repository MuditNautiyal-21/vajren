# 02 - llama.cpp, for whichever backend this machine actually needs.
#
# Reads config/hardware.json (written by core/hardware.py) and fetches the
# matching release asset. Nothing here is AMD-specific - on an NVIDIA box it
# picks a CUDA build, on a CPU-only box it picks the CPU build.
#
# WHY VULKAN ON *THIS* MACHINE: AMD's HIP SDK requires Windows 11 and lists
# gfx1031 (RX 6750 XT) as unsupported even there; ROCm on WSL2 dropped RDNA2 as
# well. Vulkan needs nothing but the stock Adrenalin driver and benchmarks at
# ~82 tok/s generation on this exact card. Ignore every HSA_OVERRIDE guide.
$ErrorActionPreference = "Stop"
$root = "C:\vajren"
$dest = "$root\llama"

$hwFile = "$root\config\hardware.json"
if (-not (Test-Path $hwFile)) {
  Write-Host "No config\hardware.json - run:  python core\hardware.py" -ForegroundColor Red
  exit 1
}
$hw = Get-Content $hwFile -Raw | ConvertFrom-Json
Write-Host ("backend: {0}   ({1})" -f $hw.backend, $hw.backend_reason) -ForegroundColor Cyan

# asset name fragment per backend, Windows x64
$want = switch ($hw.backend) {
  "cuda"   { "bin-win-cuda" }      # CUDA 12 and 13 variants ship; cu11 was dropped in 2026
  "vulkan" { "bin-win-vulkan-x64" }
  "hip"    { "bin-win-hip" }
  "sycl"   { "bin-win-sycl" }
  default  { "bin-win-cpu-x64" }
}

# Do NOT use /releases/latest. llama.cpp's build releases are tagged bNNNNN, and
# /latest can point at an unrelated tag with no binaries attached (it returned
# v0.3.0 with zero Windows assets when this was written). Walk the release list
# and take the newest one that actually carries the asset we need.
Write-Host "`nfinding the newest release with a $want build..." -ForegroundColor Cyan
$rels = Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15"
$rel = $null; $asset = $null
foreach ($r in $rels) {
  # exclude the cudart runtime bundle, which also matches "win-cuda"
  $a = $r.assets | Where-Object { $_.name -like "*$want*" -and $_.name -like "*.zip" -and $_.name -notlike "cudart-*" } | Select-Object -First 1
  if ($a) { $rel = $r; $asset = $a; break }
}
if (-not $asset) {
  Write-Host "`nNo '*$want*' asset in the last 15 releases. Available in $($rels[0].tag_name):" -ForegroundColor Yellow
  $rels[0].assets | Where-Object { $_.name -like "*win*" } | ForEach-Object { Write-Host ("    " + $_.name) }
  exit 1
}
Write-Host ("  tag: {0}" -f $rel.tag_name)
Write-Host ("  asset: {0}  ({1:N0} MB)" -f $asset.name, ($asset.size / 1MB))

$zip = Join-Path $env:TEMP $asset.name
Invoke-WebRequest $asset.browser_download_url -OutFile $zip
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Expand-Archive -Path $zip -DestinationPath $dest -Force
Remove-Item $zip

# Some builds nest everything one level down; flatten so paths stay predictable.
if (-not (Test-Path "$dest\llama-server.exe")) {
  $inner = Get-ChildItem $dest -Directory | Where-Object { Test-Path "$($_.FullName)\llama-server.exe" } | Select-Object -First 1
  if ($inner) { Get-ChildItem $inner.FullName | Move-Item -Destination $dest -Force; Remove-Item $inner.FullName -Recurse -Force }
}

Write-Host "`nverifying the GPU is visible to this build..." -ForegroundColor Cyan
& "$dest\llama-server.exe" --list-devices

# Pin it, so a future regression is traceable. Vulkan support has broken before.
$rel.tag_name | Out-File "$root\config\llama-version.txt" -Encoding ascii -NoNewline
Write-Host ("`npinned {0} in config\llama-version.txt" -f $rel.tag_name) -ForegroundColor Green
