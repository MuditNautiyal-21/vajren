# 01 - Python environment. Keep VAJREN in its own env; the voice stack is fussy
# about ABI and will fight your base Anaconda install otherwise.
$ErrorActionPreference = "Stop"
$root = "C:\Users\ytdek\vajren"

Write-Host "Creating conda env 'vajren' (python 3.12)..." -ForegroundColor Cyan
conda create -n vajren python=3.12 -y

Write-Host "`nInstalling core dependencies..." -ForegroundColor Cyan
conda run -n vajren python -m pip install --upgrade pip
conda run -n vajren python -m pip install -r "$root\requirements.txt"

Write-Host "`nSeeding .env from template (if absent)..." -ForegroundColor Cyan
if (-not (Test-Path "$root\.env")) {
  Copy-Item "$root\.env.example" "$root\.env"
  Write-Host "  .env created - fill in your keys" -ForegroundColor Yellow
} else {
  Write-Host "  .env already exists, leaving it alone"
}

Write-Host "`nInitialising git (skills and config are version-controlled)..." -ForegroundColor Cyan
if (-not (Test-Path "$root\.git")) {
  git -C $root init
  git -C $root add .gitignore README.md config docs scripts skills tests core
  git -C $root commit -m "VAJREN: initial scaffold"
} else {
  Write-Host "  git already initialised"
}

Write-Host "`nDone. Activate with:  conda activate vajren`n" -ForegroundColor Green
