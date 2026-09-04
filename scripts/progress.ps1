# Where are the model downloads up to?
#
#   .\scripts\progress.ps1          one snapshot
#   .\scripts\progress.ps1 -Watch   refresh every 15s until you Ctrl+C
param([switch]$Watch)

$root   = "C:\vajren"
$models = "$root\models"

# name -> expected size in GB
$expect = @{
  "Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf" = 2.3
  "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"          = 22.1
  "Qwen3VL-8B-Instruct-Q4_K_M.gguf"         = 6.0
  "GLM-4.7-Flash-UD-Q4_K_XL.gguf"           = 17.5
  "google_gemma-4-31B-it-Q4_K_M.gguf"       = 18.5
}

function Show {
  Clear-Host
  Write-Host "VAJREN - model downloads    $(Get-Date -Format 'HH:mm:ss')`n" -ForegroundColor Cyan

  $running = Get-Process python -EA SilentlyContinue |
             Where-Object { $_.Path -like "*vajren*" }
  Write-Host ("  downloader: " + $(if ($running) { "running (pid $($running[0].Id))" } else { "NOT RUNNING" })) `
    -ForegroundColor $(if ($running) { "Green" } else { "Red" })
  Write-Host ""

  foreach ($name in $expect.Keys | Sort-Object) {
    $done = Join-Path $models $name
    $part = "$done.part"
    $gb   = $expect[$name]
    $short = if ($name.Length -gt 40) { $name.Substring(0, 37) + "..." } else { $name }

    if (Test-Path $done) {
      Write-Host ("  {0,-40}  COMPLETE  {1,6:N1} GB" -f $short, ((Get-Item $done).Length / 1GB)) -ForegroundColor Green
    } elseif (Test-Path $part) {
      $have = (Get-Item $part).Length / 1GB
      $pct  = [math]::Min(100, $have / $gb * 100)
      $bar  = ("#" * [int]($pct / 4)).PadRight(25, ".")
      Write-Host ("  {0,-40}  [{1}] {2,5:N1}%  {3,5:N2} / {4,4:N1} GB" -f $short, $bar, $pct, $have, $gb) -ForegroundColor Yellow
    } else {
      Write-Host ("  {0,-40}  not started         {1,4:N1} GB" -f $short, $gb) -ForegroundColor DarkGray
    }
  }

  Write-Host "`n  last lines from the log:" -ForegroundColor Cyan
  if (Test-Path "$root\logs\fetch.log") {
    Get-Content "$root\logs\fetch.log" -Tail 4 | ForEach-Object { Write-Host "    $_" }
  } else { Write-Host "    (no log yet)" }

  if (-not $running) {
    Write-Host "`n  To restart (it resumes from where it stopped):" -ForegroundColor Yellow
    Write-Host "    .\scripts\resume-download.ps1"
  }
}

if ($Watch) { while ($true) { Show; Start-Sleep 15 } } else { Show }
