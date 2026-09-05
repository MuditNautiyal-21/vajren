# 99 - Run everything. One command before any commit.
#
# Ordered cheapest-first so a broken foundation fails in seconds rather than
# after four minutes of model loading.
$ErrorActionPreference = "Continue"
Set-Location C:\vajren
$py = "C:\vajren\.venv\Scripts\python.exe"
$fail = 0
$results = @()

function Run($name, $cmd, $argv) {
  Write-Host ("`n=== {0} " -f $name).PadRight(72, "=") -ForegroundColor Cyan
  & $cmd @argv
  $ok = ($LASTEXITCODE -eq 0)
  $script:results += [pscustomobject]@{ Suite = $name; Result = $(if ($ok) { "PASS" } else { "FAIL" }) }
  if (-not $ok) { $script:fail++ }
}

Run "tools    (40 assertions, no model)" $py @("-u", "scripts\08-tools-test.py")
Run "voice    (round trip + confirmation parsing)" $py @("-u", "scripts\17-voice-roundtrip.py")
Run "swap     (3 models on one card)" $py @("-u", "scripts\12-swap-test.py")
Run "smoke    (gateway end to end)" $py @("-u", "scripts\06-smoke-test.py")
Run "loop     (approve + cancel)" $py @("-u", "scripts\09-loop-test.py")
Run "injection(3 attacks, must all fail)" $py @("-u", "scripts\18-injection-test.py")

Write-Host "`n" + ("=" * 72) -ForegroundColor Cyan
$results | Format-Table -AutoSize
if ($fail -eq 0) { Write-Host "ALL SUITES PASS" -ForegroundColor Green }
else { Write-Host "$fail SUITE(S) FAILED" -ForegroundColor Red }
exit $fail
