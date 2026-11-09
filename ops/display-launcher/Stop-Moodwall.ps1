[CmdletBinding()]
param()

# "무드월 끄기" for library staff: close the wall window, stop the Moodwall containers
# (data stays in the Docker volume), and say it is safe to shut the laptop down.
# Students who scan afterwards get the existing "display offline" answer once the
# 30-second online lease lapses; a ticket QR also expires on its own within 10 minutes.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1')) -Force -DisableNameChecking
Add-Type -AssemblyName System.Windows.Forms

function Show-StaffMessage {
    param([string]$Message)
    [void][Windows.Forms.MessageBox]::Show($Message, '무드월 끄기', [Windows.Forms.MessageBoxButtons]::OK, [Windows.Forms.MessageBoxIcon]::Information)
}

$failed = $false
try {
    $config = Read-MoodwallConfiguration -InstallRoot $PSScriptRoot
    $plan = Get-MoodwallLaunchPlan -DisplayUrl $config.displayUrl -InstallRoot $PSScriptRoot -DesktopDirectory ([Environment]::GetFolderPath('DesktopDirectory'))
    # Only the dedicated Moodwall profile is closed; staff work browsers stay open.
    $owned = @(Select-MoodwallProfileProcesses -ProfileDirectory $plan.ProfileDirectory -BrowserPath $plan.BrowserPath -Processes @(Get-MoodwallBrowserProcesses $plan.BrowserPath))
    foreach ($entry in $owned) { try { [void][Diagnostics.Process]::GetProcessById([int]$entry.Id).CloseMainWindow() } catch { } }
    $until = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $until -and @($owned | Where-Object { try { -not [Diagnostics.Process]::GetProcessById([int]$_.Id).HasExited } catch { $false } }).Count) {
        Start-Sleep -Milliseconds 300
    }
    # Staff explicitly asked to turn the wall off: a window that ignored the close request is ended.
    foreach ($entry in $owned) { try { Stop-Process -Id ([int]$entry.Id) -Force -ErrorAction Stop } catch { } }
    if ([IO.File]::Exists($plan.StaffSessionPath)) { [IO.File]::Delete($plan.StaffSessionPath) }

    $server = Read-MoodwallServerConfiguration -InstallRoot $PSScriptRoot
    if ($server) {
        $docker = Resolve-MoodwallDocker
        $arguments = (Get-MoodwallComposeArguments $server @('stop')) | ForEach-Object { if ($_ -match '[\s"]') { '"' + $_ + '"' } else { $_ } }
        $process = Start-Process -FilePath $docker -ArgumentList ($arguments -join ' ') -WindowStyle Hidden -PassThru
        if (-not $process.WaitForExit(120000) -or $process.ExitCode -ne 0) { $failed = $true }
    }
    Write-MoodwallLauncherEvent $PSScriptRoot POWER_OFF
} catch { $failed = $true }

if ($failed) {
    Write-MoodwallLauncherEvent $PSScriptRoot POWER_OFF_FAILED
    Show-StaffMessage "전광판 화면은 닫았어요. 이제 노트북을 꺼도 괜찮아요.`r`n(서버 정리 중 문제가 있었지만, 노트북을 끄면 함께 정리돼요.)"
} else {
    Show-StaffMessage '무드월을 껐어요. 이제 노트북을 꺼도 괜찮아요.'
}
