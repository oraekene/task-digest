# Register (or remove) the scheduled task that runs the monthly state audit.
#
#   pwsh -NoProfile -ExecutionPolicy Bypass -File register_audit_task.ps1
#   pwsh -NoProfile -ExecutionPolicy Bypass -File register_audit_task.ps1 -Time 13:30
#   pwsh -NoProfile -ExecutionPolicy Bypass -File register_audit_task.ps1 -Remove
#
# Windows has no monthly trigger in the PowerShell scheduler cmdlets, and a monthly
# trigger would simply be skipped if the laptop were off that day. So the task fires
# every day and "start when available" catches a missed start. run_audit_monthly.ps1
# asks audit_run.py, which exits at once unless 30 days have passed since the last
# good audit. The interval is the audit_interval_days key in settings.json.
#
# The task runs as you, only while you are logged on, because the agent (opencode)
# uses your own sign-in. It is allowed up to 6 hours, and a second copy never starts
# beside a running one.
param(
    [string]$Time = '13:00',
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$name = 'TaskDigest Audit'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed the scheduled task '$name'."
    } else {
        Write-Host "No scheduled task named '$name'."
    }
    return
}

$here = $PSScriptRoot
$script = Join-Path $here 'run_audit_monthly.ps1'
if (-not (Test-Path -LiteralPath $script)) { throw "Cannot find $script" }

$shell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $shell) { $shell = (Get-Command powershell.exe).Source }

$action = New-ScheduledTaskAction -Execute $shell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Monthly state audit of task-digest (audit_run.py). Advisory only.' -Force | Out-Null

Write-Host "Registered '$name': checks daily at $Time, audits when 30 days have passed."
Write-Host "Run it now with:  pwsh -NoProfile -ExecutionPolicy Bypass -File run_audit_monthly.ps1 -Force"
