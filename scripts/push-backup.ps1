# Push both repos to both of their remotes. Run at the end of a session.
#
#   code    C:\vajren          origin -> github.com/MuditNautiyal-21/vajren        (PUBLIC)
#                                          backup -> F:\Programs\AI\VAJREN.git                 (T7)
#   journal C:\vajren\private   origin -> github.com/MuditNautiyal-21/vajren-journal (PRIVATE)
#                                          backup -> F:\Programs\AI\VAJREN-journal.git         (T7)
#
# The journal is deliberately a separate repo so it can never be committed to the
# public one by accident. private/ is gitignored in the code repo.
#
# Recovery on a fresh machine:
#   git clone F:\Programs\AI\VAJREN.git          C:\Users\<you>\vajren
#   git clone F:\Programs\AI\VAJREN-journal.git  C:\Users\<you>\vajren\private
#   .\scripts\01-setup-python.ps1 ; .\scripts\02-get-runtime.ps1 ; .\scripts\03-get-models.ps1
$ErrorActionPreference = "Continue"

$repos = @(
  @{ name = "code";    path = "C:\vajren";         bare = "F:\Programs\AI\VAJREN.git" },
  @{ name = "journal"; path = "C:\vajren\private"; bare = "F:\Programs\AI\VAJREN-journal.git" }
)

$fail = 0
foreach ($r in $repos) {
  Write-Host "`n=== $($r.name) ===" -ForegroundColor Cyan
  Set-Location $r.path

  $dirty = git status --porcelain
  if ($dirty) {
    Write-Host "  uncommitted changes - commit first:" -ForegroundColor Yellow
    git status --short
    $fail++
    continue
  }

  foreach ($remote in @("origin", "backup")) {
    $out = git push $remote main 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Host "  $remote  ok" -ForegroundColor Green }
    else { Write-Host "  $remote  FAILED" -ForegroundColor Red; $out | Select-Object -Last 3; $fail++ }
  }

  if (-not (Test-Path (Join-Path $r.bare "HEAD"))) {
    Write-Host "  ! T7 not present - the offline copy of '$($r.name)' is now STALE." -ForegroundColor Yellow
  }
}

if ($fail -eq 0) { Write-Host "`nboth repos pushed to both remotes." -ForegroundColor Green }
else { Write-Host "`n$fail problem(s) above." -ForegroundColor Red }
