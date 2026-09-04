# 01 - Python environment.
#
# VAJREN targets Python 3.10-3.13. Not 3.14: several dependencies it needs
# (onnxruntime, sounddevice, some numpy builds) do not publish 3.14 wheels yet,
# and pip falls back to building from source, which on Windows means a compiler
# you probably do not have.
#
# So this prefers a plain venv off a suitable system Python - portable, no
# Anaconda required - and only falls back to conda when the system Python is
# outside the supported range. That keeps VAJREN installable on a machine that
# has never heard of conda, which is the whole point of bootstrap.py.
$ErrorActionPreference = "Continue"
$root = "C:\vajren"
$venv = "$root\.venv"
$MIN = [version]"3.10"
$MAX = [version]"3.14"      # exclusive

function Try-Python($exe) {
  if (-not $exe) { return $null }
  try {
    $v = & $exe -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
    if (-not $v) { return $null }
    $ver = [version]$v
    if ($ver -ge $MIN -and $ver -lt $MAX) { return @{ exe = $exe; ver = $ver } }
  } catch { }
  return $null
}

Write-Host "Looking for a Python between 3.10 and 3.13..." -ForegroundColor Cyan
$found = $null
foreach ($c in @("python", "python3", "python3.13", "python3.12", "python3.11")) {
  $cmd = (Get-Command $c -EA SilentlyContinue).Source
  $found = Try-Python $cmd
  if ($found) { break }
}
# Windows launcher knows about versions the PATH does not.
if (-not $found -and (Get-Command py -EA SilentlyContinue)) {
  foreach ($v in @("3.13", "3.12", "3.11", "3.10")) {
    $p = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null)
    $found = Try-Python $p
    if ($found) { break }
  }
}

if ($found) {
  Write-Host ("  using {0}  (Python {1})" -f $found.exe, $found.ver) -ForegroundColor Green
  if (-not (Test-Path $venv)) { & $found.exe -m venv $venv }
  $py = "$venv\Scripts\python.exe"
} else {
  $sys = (Get-Command python -EA SilentlyContinue).Source
  $sysver = if ($sys) { & $sys -c "import sys;print('%d.%d'%sys.version_info[:2])" } else { "none" }
  Write-Host "  no suitable system Python (found: $sysver). Falling back to conda." -ForegroundColor Yellow

  $conda = $null
  foreach ($c in @("conda",
                   "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
                   "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
                   "$env:LOCALAPPDATA\miniconda3\Scripts\conda.exe",
                   "C:\ProgramData\anaconda3\Scripts\conda.exe")) {
    $p = if (Test-Path $c) { $c } else { (Get-Command $c -EA SilentlyContinue).Source }
    if ($p) { $conda = $p; break }
  }
  if (-not $conda) {
    Write-Host "  No Python 3.10-3.13 and no conda." -ForegroundColor Red
    Write-Host "  Install Python 3.12 from python.org, then re-run." -ForegroundColor Red
    exit 1
  }
  Write-Host "  conda: $conda" -ForegroundColor Green
  & $conda create -n vajren python=3.12 -y
  $py = (& $conda run -n vajren python -c "import sys;print(sys.executable)").Trim()
}

Write-Host "`nInterpreter: $py" -ForegroundColor Cyan
& $py -c "import sys;print('  version', sys.version.split()[0])"

Write-Host "`nInstalling dependencies..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r "$root\requirements.txt"

if (-not (Test-Path "$root\.env")) {
  Copy-Item "$root\.env.example" "$root\.env"
  Write-Host "`n  .env created from the template - fill in keys if you want a cloud fallback" -ForegroundColor Yellow
}

# Record the interpreter so every other script uses the same one.
$py | Out-File "$root\config\python-path.txt" -Encoding ascii -NoNewline
Write-Host "`n  interpreter recorded in config\python-path.txt"
Write-Host "`nDone. Verify with:" -ForegroundColor Green
Write-Host "  & (Get-Content C:\vajren\config\python-path.txt) C:\vajren\core\hardware.py"
