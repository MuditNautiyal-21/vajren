# 11 - Install llama-swap.
#
# WHY NOW: two models fit on this card at once; three do not. The workhorse
# alone holds ~11 of 12 GB. llama-swap loads the right llama-server on demand
# and unloads the last one, which is the only way to have a bench of
# specialists on a single mid-range card.
#
# Walks /releases rather than /releases/latest - the "latest" tag has pointed at
# a release with no Windows assets before (J-028).
$ErrorActionPreference = "Stop"
$root = "C:\vajren"
$dest = "$root\bin"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host "  querying releases..." -ForegroundColor Cyan
$releases = Invoke-RestMethod "https://api.github.com/repos/mostlygeek/llama-swap/releases" `
  -Headers @{ "User-Agent" = "vajren" }

$asset = $null
foreach ($r in $releases) {
  $asset = $r.assets | Where-Object { $_.name -match "windows" -and $_.name -match "amd64" -and $_.name -match "\.zip$" } | Select-Object -First 1
  if ($asset) { $tag = $r.tag_name; break }
}
if (-not $asset) { Write-Host "  no Windows asset found in any release" -ForegroundColor Red; exit 1 }

Write-Host ("  {0}  ({1})  {2:N1} MB" -f $asset.name, $tag, ($asset.size/1MB)) -ForegroundColor Cyan
$zip = "$env:TEMP\llama-swap.zip"
Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
Expand-Archive $zip -DestinationPath $dest -Force
Remove-Item $zip -Force

$exe = Get-ChildItem $dest -Filter "llama-swap*.exe" -Recurse | Select-Object -First 1
if (-not $exe) { Write-Host "  no llama-swap.exe in the archive" -ForegroundColor Red; exit 1 }
if ($exe.Name -ne "llama-swap.exe") { Move-Item $exe.FullName "$dest\llama-swap.exe" -Force }

Set-Content "$root\config\llama-swap-version.txt" $tag
Write-Host ("  installed  {0}\llama-swap.exe  ({1})" -f $dest, $tag) -ForegroundColor Green
& "$dest\llama-swap.exe" --version
