# 14 - Drive core.main through a real conversation by piping stdin.
#
# The point is turn 2: "add a second line to that same file" names no path.
# It can only work if the conversation carried across requests, which is the
# whole reason core/main.py exists rather than scripts/ask.py.
#
# Then /undo, which is the first time the undo machinery is reachable by a human.
$ErrorActionPreference = "Continue"
Remove-Item C:\vajren\sandbox\repl-note.txt -EA SilentlyContinue

$turns = @(
  'Create a file at C:\vajren\sandbox\repl-note.txt containing the single line: first'
  'yes go ahead'
  'Add a second line saying "second" to that same file, keeping the first line'
  'yes go ahead'
  '/steps'
  '/undo'
  'yes go ahead'
  'quit'
)

# -m core.main resolves the package from the CURRENT directory, so this must
# run from the project root or Python cannot find `core` at all.
Set-Location C:\vajren
$turns -join "`n" | & C:\vajren\.venv\Scripts\python.exe -u -m core.main

Write-Host "`n=== repl-note.txt after the conversation ===" -ForegroundColor Cyan
if (Test-Path C:\vajren\sandbox\repl-note.txt) {
  Get-Content C:\vajren\sandbox\repl-note.txt | ForEach-Object { Write-Host "  $_" }
  Write-Host "`n  (expected: just 'first' - the second line was added, then undone)" -ForegroundColor DarkGray
} else {
  Write-Host "  MISSING" -ForegroundColor Red
}
