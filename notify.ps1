param([string]$Status = '')
# Show a Windows toast with the top tasks from the newest digest file.
# Falls back to a 60-second popup if the toast API is unavailable.
$ErrorActionPreference = 'Continue'

$topN = 3
try { $topN = [int]((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'settings.json') -Raw | ConvertFrom-Json).notify_top) } catch { }

$latest = Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'digest-*.md' | Sort-Object Name -Descending | Select-Object -First 1
if (-not $latest) { exit 0 }

$top = @()
$inFocus = $false
foreach ($line in Get-Content -LiteralPath $latest.FullName) {
    if ($line -match "^## Today's focus") { $inFocus = $true; continue }
    if ($inFocus -and $line -match '^## ') { break }
    if ($inFocus -and $line -match '^\d+\.\s+(.+)$') {
        $item = $Matches[1] -replace '\s+\(from note:.*$', ''
        if ($item.Length -gt 90) { $item = $item.Substring(0, 87) + '...' }
        $top += $item
    }
    if ($top.Count -ge $topN) { break }
}
if ($top.Count -eq 0) { $top = @('No open tasks. Relax.') }

$title = "Today's focus - $($latest.BaseName -replace 'digest-','')"
$body = ($top | ForEach-Object { "- $_" }) -join "`n"
if ($Status) { $body = "$Status`n$body" }

function Escape-Xml([string]$s) {
    $s.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;')
}

$shown = $false
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $xmlText = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>$(Escape-Xml $title)</text>
      <text>$(Escape-Xml $body)</text>
    </binding>
  </visual>
</toast>
"@
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml($xmlText)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
    $shown = $true
} catch {
    $shown = $false
}

if (-not $shown) {
    try {
        $msg = "$title`n`n$body"
        (New-Object -ComObject Wscript.Shell).Popup($msg, 60, 'Task digest') | Out-Null
    } catch { }
}
