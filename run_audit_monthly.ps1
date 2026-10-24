# Monthly state audit. Safe to run by hand at any time.
#
# The scheduled task "TaskDigest Audit" starts this every day. audit_run.py does
# nothing, and this script ends quickly, unless 30 days have passed since the last
# good audit (or a failed batch is waiting for a retry).
#
#   pwsh -NoProfile -ExecutionPolicy Bypass -File run_audit_monthly.ps1
#   pwsh -NoProfile -ExecutionPolicy Bypass -File run_audit_monthly.ps1 -Force
#   pwsh -NoProfile -ExecutionPolicy Bypass -File run_audit_monthly.ps1 -Force -Scope full
#
# Steps: audit_run.py (scope, one agent per batch, merge) -> commit and push
# audit-report.json and audit-state.json -> toast. It never edits tasks.json.
param(
    [switch]$Force,
    [ValidateSet('auto', 'full', 'monthly', 'retry')][string]$Scope = 'auto'
)
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
$logDir = Join-Path $here 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'audit_monthly.log'
$py = Join-Path $here '.venv\Scripts\python.exe'

function Note($msg) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Add-Content -LiteralPath $log }

if (-not (Test-Path -LiteralPath $py)) {
    Note "cannot find $py; set up the virtual environment first"
    & (Join-Path $here 'notify.ps1') -Status 'Monthly audit could not start: no Python environment'
    exit 1
}

$pyArgs = @((Join-Path $here 'audit_run.py'), '--scope', $Scope)
if ($Force) { $pyArgs += '--force' }

$global:LASTEXITCODE = $null
$out = & $py @pyArgs 2>&1
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 1 }
$text = ($out | ForEach-Object { "$_" }) -join "`n"

# Daily check with nothing to do: say nothing, touch nothing.
if ($code -eq 0 -and $text -match 'audit not due') {
    if ($Force) { Write-Host $text }
    exit 0
}

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') audit check ===" | Add-Content -LiteralPath $log
$text | Add-Content -LiteralPath $log
Write-Host $text

if ($code -ne 0) {
    Note "audit failed with exit code $code; it will be tried again on the next daily check"
    & (Join-Path $here 'notify.ps1') -Status 'Monthly audit failed, will retry tomorrow'
    exit $code
}

# Another run holds a lock: nothing changed, nothing to commit.
if ($text -match 'another audit is running|morning run is active') { exit 0 }

try {
    git -C $here add audit-report.json audit-state.json 2>> $log
    git -C $here diff --cached --quiet 2>> $log
    if ($LASTEXITCODE -ne 0) {
        git -C $here commit -q -m "Monthly audit $(Get-Date -Format 'yyyy-MM-dd')" 2>> $log
        Note 'audit commit made'
        git -C $here push -q 2>> $log
        if ($LASTEXITCODE -eq 0) { Note 'pushed' } else { Note 'push failed, the login task or the next daily run will push it' }
    }
} catch {
    Note "git step failed: $_"
}

$status = 'Monthly audit done'
$done = $text -split "`n" | Where-Object { $_ -match 'audit done:' } | Select-Object -Last 1
if ($done) { $status = ($done -replace '^\S+ \S+ ', '') }
& (Join-Path $here 'notify.ps1') -Status $status
