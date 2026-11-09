[CmdletBinding()]
param([Alias('ValidateOnly', 'DryRun')][switch]$PlanOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1')) -Force -DisableNameChecking

try {
    $config = Read-MoodwallConfiguration -InstallRoot $PSScriptRoot
    $plan = Get-MoodwallLaunchPlan -DisplayUrl $config.displayUrl -InstallRoot $PSScriptRoot -DesktopDirectory ([Environment]::GetFolderPath('DesktopDirectory'))
    if ($PlanOnly) { $plan | ConvertTo-Json -Depth 5; exit 0 }
    Assert-MoodwallDeploymentUrl -DisplayUrl $plan.DisplayUrl
    Assert-MoodwallLocalPath -Path $plan.ProfileDirectory

    # A short mutex prevents two simultaneous clicks from both spawning windows.
    $mutex = New-Object Threading.Mutex($false, (Get-MoodwallLauncherMutexName $plan.ProfileDirectory))
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { Write-MoodwallLauncherEvent $PSScriptRoot BUSY; exit 0 }
        $processes = @(Get-MoodwallBrowserProcesses $plan.BrowserPath)
        $existing = Get-MoodwallLaunchDecision -Plan $plan -Processes $processes -StaffSessionExists ([IO.File]::Exists($plan.StaffSessionPath))
        if ($existing.Action -eq 'StaffWindowOpen') { Write-MoodwallLauncherEvent $PSScriptRoot STAFF_WINDOW_OPEN; exit 0 }
        if ($existing.Action -eq 'Activate') {
            $shell = New-Object -ComObject WScript.Shell
            if (-not $shell.AppActivate([int]$existing.Process.Id)) {
                Write-MoodwallLauncherEvent $PSScriptRoot ACTIVATE_FAILED
            } else { Write-MoodwallLauncherEvent $PSScriptRoot ACTIVATED }
            exit 0
        }
        if ($existing.Action -eq 'AlreadyRunning') {
            Write-MoodwallLauncherEvent $PSScriptRoot PROFILE_RUNNING
            exit 0
        }
        # A closed staff window is required before the public role can resume.
        # Never activate a known staff window or reload an existing public playback.
        Assert-MoodwallLocalPath $plan.StaffSessionPath
        if ([IO.File]::Exists($plan.StaffSessionPath)) { [IO.File]::Delete($plan.StaffSessionPath) }
        if (-not (Wait-MoodwallEndpoint -DisplayUrl $plan.DisplayUrl)) {
            Write-MoodwallLauncherEvent $PSScriptRoot ENDPOINT_UNAVAILABLE
            exit 1
        }
        [void][IO.Directory]::CreateDirectory($plan.ProfileDirectory)
        # The employee must see the browser; only the PowerShell shortcut helper is hidden.
        # Open on the LED wall, not on the laptop's own screen (extended desktop mode).
        $windowArguments = @()
        try { $windowArguments = @(Get-MoodwallWindowArguments (Select-MoodwallTargetScreen @(Get-MoodwallScreens))) } catch { }
        $browserArguments = Add-MoodwallWindowArguments -BrowserArguments $plan.BrowserArguments -WindowArguments $windowArguments
        $browser = Start-Process -FilePath $plan.BrowserPath -ArgumentList ($browserArguments -join ' ') -WindowStyle Normal -PassThru
        Write-MoodwallLauncherEvent $PSScriptRoot STARTED
        # Retain the launch fence while Chromium creates its own process singleton.
        try { [void]$browser.WaitForInputIdle(5000) } catch { } # Browser singleton handoff can end the first process.
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
} catch {
    if ($PlanOnly) { Write-Error $_.Exception.Message; exit 1 }
    # Public launch must be silent even for invalid config, DNS/TLS and local failures.
    Write-MoodwallLauncherEvent $PSScriptRoot LAUNCH_FAILED
    exit 1
}
