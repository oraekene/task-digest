# Start at login: wait for the internet, catch up a missed morning run,
# then start the console and the sticky note.
# Safe to run by hand.
param([int]$TimeoutMinutes = 60)
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
$logDir = Join-Path $here 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'login.log'
$py = Join-Path $here '.venv\Scripts\python.exe'
$pythonw = Join-Path $here '.venv\Scripts\pythonw.exe'

function Note($msg) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Add-Content -LiteralPath $log }

function Test-Online {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $task = $client.ConnectAsync('api.simperium.com', 443)
        $ok = $task.Wait(5000) -and $client.Connected
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

function Test-Port($port) {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $task = $client.ConnectAsync('127.0.0.1', $port)
        $ok = $task.Wait(1000) -and $client.Connected
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

Note 'login start'
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
while (-not (Test-Online)) {
    if ((Get-Date) -gt $deadline) {
        Note "no internet after $TimeoutMinutes minutes, starting local apps only"
        break
    }
    Note 'waiting for internet'
    Start-Sleep -Seconds 30
}

if (Test-Online) {
    $stamp = Join-Path $logDir 'last_success.txt'
    $today = Get-Date -Format 'yyyy-MM-dd'
    $ranToday = (Test-Path $stamp) -and ((Get-Content -LiteralPath $stamp -Raw).Trim() -eq $today)
    if (-not $ranToday) {
        Note 'no successful run today, running now'
        & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here 'run_daily.ps1') *>> $log
    } else {
        Note 'morning run already succeeded today'
    }
    git -C $here push -q 2>> $log
}

if (-not (Test-Port 8765)) {
    Start-Process -FilePath $pythonw -ArgumentList 'console.py', '--no-open' -WorkingDirectory $here
    Note 'console started'
}

$sticky = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*sticky.py*' } | Select-Object -First 1
if (-not $sticky) {
    Start-Process -FilePath $pythonw -ArgumentList 'sticky.py' -WorkingDirectory $here
    Note 'sticky started'
}

Note 'login done'