# Morning run. Safe to run by hand at any time.
# Order: read-only gate, token, sync, extract new tasks, check-ins, render, notify.
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
$logDir = Join-Path $here 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'run_daily.log'
$py = Join-Path $here '.venv\Scripts\python.exe'
$settings = Get-Content -LiteralPath (Join-Path $here 'settings.json') -Raw | ConvertFrom-Json

function Note($msg) { "$(Get-Date -Format 'HH:mm:ss') $msg" | Add-Content -LiteralPath $log }

$lock = Join-Path $logDir 'run.lock'
if ((Test-Path $lock) -and ((Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime).TotalMinutes -lt 30) {
    Note 'another run is active, stopping'
    exit 0
}
Set-Content -LiteralPath $lock -Value (Get-Date -Format o)

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') run start ===" | Add-Content -LiteralPath $log

$gate = & $py (Join-Path $here 'gate.py') 2>&1
Note "gate: $gate"
if ($LASTEXITCODE -ne 0) {
    Note 'gate failed, stopping'
    Remove-Item -LiteralPath $lock -ErrorAction SilentlyContinue
    & (Join-Path $here 'notify.ps1') -Status 'Read-only gate failed'
    exit 1
}

& $py (Join-Path $here 'get_token.py') *>> $log
& $py (Join-Path $here 'sync_notes.py') *>> $log

$status = ''
$changed = 0
try {
    $changes = Get-Content -LiteralPath (Join-Path $here 'pending_changes.json') -Raw | ConvertFrom-Json
    $changed = @($changes.added).Count + @($changes.updated).Count
} catch { }

if ($changed -gt 0 -and $settings.auto_extract) {
    Remove-Item -LiteralPath (Join-Path $here 'tasks_new.json') -ErrorAction SilentlyContinue
    & $py (Join-Path $here 'dump_notes.py') *>> $log
    Note "changed notes: $changed, extracting"
    $out = & opencode run --auto --pure --dir $here "Run the extraction now. Follow the attached prompt." -f (Join-Path $here 'extract_prompt.md') 2>&1
    $out | Add-Content -LiteralPath $log
    if (Test-Path (Join-Path $here 'tasks_new.json')) {
        $merge = & $py (Join-Path $here 'merge_pending.py') 2>&1
        Note "merge: $merge"
        $status = "extracted from $changed new notes"
    } else {
        Note 'extraction produced no file'
        $status = "$changed new notes need extraction (run failed)"
    }
} elseif ($changed -gt 0) {
    $status = "$changed new notes waiting for extraction"
} else {
    $status = 'no new notes'
}

& $py (Join-Path $here 'checkins.py') run *>> $log
& $py (Join-Path $here 'tracker.py') digest *>> $log

$stamp = Join-Path $logDir 'last_success.txt'
Get-Date -Format 'yyyy-MM-dd' | Set-Content -LiteralPath $stamp

try {
    git -C $here add tasks.json list.md today.json marker-report.md marker-report.json 'digest-*.md' 2>> $log
    git -C $here diff --cached --quiet 2>> $log
    if ($LASTEXITCODE -ne 0) {
        git -C $here commit -q -m "Daily digest $(Get-Date -Format 'yyyy-MM-dd')" 2>> $log
        Note 'daily commit made'
    }
    git -C $here push -q 2>> $log
    if ($LASTEXITCODE -eq 0) { Note 'pushed' } else { Note 'push failed, will retry at login' }
} catch {
    Note "git step failed: $_"
}

& (Join-Path $here 'notify.ps1') -Status $status
Remove-Item -LiteralPath $lock -ErrorAction SilentlyContinue
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') run end ===" | Add-Content -LiteralPath $log