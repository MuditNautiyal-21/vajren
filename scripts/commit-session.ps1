# Commit both repos using prepared message files, then show status.
# Message files live in logs\ (gitignored) so they never get committed.
$ErrorActionPreference = "Continue"

Set-Location C:\vajren
Write-Host "=== code ===" -ForegroundColor Cyan
git add -A
git status --short
git commit -F C:\vajren\logs\msg-code.txt

Set-Location C:\vajren\private
Write-Host "`n=== journal ===" -ForegroundColor Cyan
git add -A
git status --short
git commit -F C:\vajren\logs\msg-journal.txt

Set-Location C:\vajren
Write-Host "`n=== verify: journal must NOT appear in the code repo ===" -ForegroundColor Cyan
$leak = git ls-files | Select-String -Pattern "^private/"
if ($leak) { Write-Host "  LEAK: $leak" -ForegroundColor Red }
else { Write-Host "  clean - no private/ files tracked in the public repo" -ForegroundColor Green }
