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
Run "confirm  (does the gate understand a person)" $py @("-u", "scripts\24-confirm-test.py")
Run "multistep(does it DO a 2-part request, or narrate it)" $py @("-u", "scripts\25-multistep-test.py")
Run "convo    (spelling, and one approval per request)" $py @("-u", "scripts\26-conversation-test.py")
Run "browser  (Vajren's own Chrome: find, click, type, refuse)" $py @("-u", "scripts\28-browser-test.py")
Run "memory   (remembers, recalls, learns when to stop asking, sees)" $py @("-u", "scripts\29-memory-test.py")
# The last two suites need a face to talk to.
#
# ⚠ They used to just assume one was listening on 7777, which meant they passed
#   when Mudit happened to have Vajren open and failed with a connection
#   traceback when he did not — and when he DID, the test's own request landed
#   in his live session while he was mid-conversation with it. The suite now
#   runs its own face on its own port and takes it down afterwards.
$env:VAJREN_FACE_PORT = "7788"
Write-Host "`n  starting a private face on :7788 for the last two suites..." -ForegroundColor DarkGray
$face = Start-Process -FilePath $py -ArgumentList @("-m","core.server") -WorkingDirectory C:\vajren `
          -RedirectStandardOutput C:\vajren\logs\face-test.log `
          -RedirectStandardError  C:\vajren\logs\face-test.err.log `
          -WindowStyle Hidden -PassThru
$deadline = (Get-Date).AddSeconds(60); $faceUp = $false
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 700
  try { Invoke-RestMethod "http://127.0.0.1:7788/status" -TimeoutSec 3 | Out-Null; $faceUp = $true; break } catch {}
}
if (-not $faceUp) { Write-Host "  the test face did NOT start - see logs\face-test.err.log" -ForegroundColor Red }

Run "face     (websocket protocol end to end)" $py @("-u", "scripts\19-face-test.py")
Run "ui-lint  (the face's own static checks)" $py @("-u", "scripts\22-ui-lint.py")

if ($face -and -not $face.HasExited) { Stop-Process -Id $face.Id -Force -EA SilentlyContinue }
Remove-Item Env:\VAJREN_FACE_PORT -EA SilentlyContinue

Write-Host "`n" + ("=" * 72) -ForegroundColor Cyan
$results | Format-Table -AutoSize
if ($fail -eq 0) { Write-Host "ALL SUITES PASS" -ForegroundColor Green }
else { Write-Host "$fail SUITE(S) FAILED" -ForegroundColor Red }
exit $fail
