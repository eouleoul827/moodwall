[CmdletBinding()]
param([Alias('ValidateOnly', 'DryRun')][switch]$PlanOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1')) -Force -DisableNameChecking

function Show-StaffMessage {
    param([string]$Message, [switch]$Retry)
    Add-Type -AssemblyName System.Windows.Forms
    $buttons = if ($Retry) { [Windows.Forms.MessageBoxButtons]::RetryCancel } else { [Windows.Forms.MessageBoxButtons]::OK }
    return [Windows.Forms.MessageBox]::Show($Message, '무드월 전광판', $buttons, [Windows.Forms.MessageBoxIcon]::Information)
}

try {
    $config = Read-MoodwallConfiguration -InstallRoot $PSScriptRoot
    $plan = Get-MoodwallLaunchPlan -DisplayUrl $config.displayUrl -InstallRoot $PSScriptRoot -DesktopDirectory ([Environment]::GetFolderPath('DesktopDirectory'))
    if ($PlanOnly) { $plan | ConvertTo-Json -Depth 5; exit 0 }
    Assert-MoodwallDeploymentUrl -DisplayUrl $plan.DisplayUrl
    Assert-MoodwallLocalPath -Path $plan.ProfileDirectory

    # A short mutex prevents two simultaneous clicks from both spawning windows.
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $suffix = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($plan.ProfileDirectory.ToLowerInvariant()))).Replace('-', '') } finally { $hash.Dispose() }
    $mutex = New-Object Threading.Mutex($false, ('Local\MoodwallDisplayLauncher-' + $suffix))
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { [void](Show-StaffMessage '전광판을 여는 중이에요. 잠시 후 확인해 주세요.'); exit 0 }
        $processName = [IO.Path]::GetFileName($plan.BrowserPath)
        $processes = @(Get-CimInstance -ClassName Win32_Process -Filter ("Name = '" + $processName + "'") | ForEach-Object {
            try {
                $running = [Diagnostics.Process]::GetProcessById($_.ProcessId)
                [pscustomobject]@{ Id = $_.ProcessId; ExecutablePath = $_.ExecutablePath; CommandLine = $_.CommandLine; MainWindowHandle = $running.MainWindowHandle }
            } catch { }
        })
        $existing = Get-MoodwallExistingDisplay -ProfileDirectory $plan.ProfileDirectory -BrowserPath $plan.BrowserPath -Processes $processes
        if ($existing.Action -eq 'Activate') {
            $shell = New-Object -ComObject WScript.Shell
            if (-not $shell.AppActivate([int]$existing.Process.Id)) {
                [void](Show-StaffMessage '전광판이 이미 실행 중이에요. 작업 표시줄에서 전광판 창을 선택해 주세요.')
            }
            exit 0
        }
        if ($existing.Action -eq 'AlreadyRunning') {
            [void](Show-StaffMessage '전광판 브라우저가 이미 실행 중이에요. 작업 표시줄에서 창을 확인해 주세요. 창이 없으면 담당자에게 알려 주세요.')
            exit 0
        }
        while (-not (Wait-MoodwallEndpoint -DisplayUrl $plan.DisplayUrl)) {
            $answer = Show-StaffMessage "서버에 연결하지 못했어요.`r`n`r`n인터넷 연결을 확인한 뒤 [다시 시도]를 눌러 주세요.`r`n계속 안 되면 담당자에게 알려 주세요.`r`n`r`n주소: $($plan.DisplayUrl)" -Retry
            if ($answer -ne [Windows.Forms.DialogResult]::Retry) { exit 1 }
        }
        [void][IO.Directory]::CreateDirectory($plan.ProfileDirectory)
        # The employee must see the browser; only the PowerShell shortcut helper is hidden.
        $browser = Start-Process -FilePath $plan.BrowserPath -ArgumentList ($plan.BrowserArguments -join ' ') -WindowStyle Normal -PassThru
        # Retain the launch fence while Chromium creates its own process singleton.
        try { [void]$browser.WaitForInputIdle(5000) } catch { } # Browser singleton handoff can end the first process.
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
} catch {
    if ($PlanOnly) { Write-Error $_.Exception.Message; exit 1 }
    [void](Show-StaffMessage ('전광판을 열지 못했어요. 담당자에게 알려 주세요.' + "`r`n`r`n" + $_.Exception.Message))
    exit 1
}
