[CmdletBinding()]
param([Alias('ValidateOnly', 'DryRun')][switch]$PlanOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1')) -Force -DisableNameChecking

function Show-StaffMessage {
    param([string]$Message)
    [void][Windows.Forms.MessageBox]::Show($Message, '무드월 직원 설정·점검', [Windows.Forms.MessageBoxButtons]::OK, [Windows.Forms.MessageBoxIcon]::Information)
}

function Open-StaffWindow {
    param([ValidateSet('Setup', 'Reopen')][string]$Role)
    $mutex = New-Object Threading.Mutex($false, (Get-MoodwallLauncherMutexName $script:Plan.ProfileDirectory))
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { Show-StaffMessage '다른 전광판 실행 작업이 진행 중이에요. 잠시 후 다시 눌러 주세요.'; return }
        $processes = @(Get-MoodwallBrowserProcesses $script:Plan.BrowserPath)
        $decision = Get-MoodwallLaunchDecision -Plan $script:Plan -Role $Role -Processes $processes -StaffSessionExists ([IO.File]::Exists($script:Plan.StaffSessionPath))
        if ($decision.Action -eq 'CloseRequired') {
            Write-MoodwallLauncherEvent $PSScriptRoot SETUP_CLOSE_REQUIRED
            Show-StaffMessage "열려 있는 무드월 전용 브라우저 창을 먼저 Alt+F4로 닫아 주세요.`r`n설정 창과 전광판 창을 함께 열지 않습니다. 업무용 브라우저는 닫을 필요가 없어요.`r`n창이 없는데 계속 이 안내가 나오면 기기 담당자에게 알려 주세요."
            return
        }
        if (-not (Wait-MoodwallEndpoint -DisplayUrl $script:Plan.DisplayUrl)) {
            Write-MoodwallLauncherEvent $PSScriptRoot ENDPOINT_UNAVAILABLE
            Show-StaffMessage "서버에 연결하지 못했어요. 인터넷과 서버 상태를 확인한 뒤 다시 눌러 주세요.`r`n이 검사는 웹페이지 응답만 확인하며 로그인·실제 전광판 출력까지 확인하지는 않아요."
            return
        }
        Assert-MoodwallLocalPath $script:Plan.ProfileDirectory
        Assert-MoodwallLocalPath $script:Plan.StaffSessionPath
        [void][IO.Directory]::CreateDirectory($script:Plan.ProfileDirectory)
        if ($Role -eq 'Setup') {
            # Written before launching: a simultaneous public click cannot reveal setup.
            [IO.File]::WriteAllText($script:Plan.StaffSessionPath, 'MoodwallStaffWindow:1', (New-Object Text.UTF8Encoding($false)))
            $arguments = $script:Plan.SetupBrowserArguments
        } else {
            if ([IO.File]::Exists($script:Plan.StaffSessionPath)) { [IO.File]::Delete($script:Plan.StaffSessionPath) }
            $arguments = $script:Plan.BrowserArguments
        }
        $browser = Start-Process -FilePath $script:Plan.BrowserPath -ArgumentList ($arguments -join ' ') -WindowStyle Normal -PassThru
        try { [void]$browser.WaitForInputIdle(5000) } catch { }
        if ($Role -eq 'Setup') { Write-MoodwallLauncherEvent $PSScriptRoot SETUP_STARTED } else { Write-MoodwallLauncherEvent $PSScriptRoot STARTED }
        # Close the local staff menu before the operator enables the public panel.
        $script:StaffForm.Close()
    } catch {
        Write-MoodwallLauncherEvent $PSScriptRoot SETUP_FAILED
        Show-StaffMessage '창을 열지 못했어요. 기기 담당자에게 진단 기록 확인을 요청해 주세요. 브라우저를 강제 종료하거나 프로필을 지우지 마세요.'
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

try {
    $config = Read-MoodwallConfiguration -InstallRoot $PSScriptRoot
    $script:Plan = Get-MoodwallLaunchPlan -DisplayUrl $config.displayUrl -InstallRoot $PSScriptRoot -DesktopDirectory ([Environment]::GetFolderPath('DesktopDirectory'))
    if ($PlanOnly) { $script:Plan | ConvertTo-Json -Depth 5; exit 0 }
    Add-Type -AssemblyName System.Windows.Forms
    Assert-MoodwallDeploymentUrl $script:Plan.DisplayUrl
    [Windows.Forms.Application]::EnableVisualStyles()
    $script:StaffForm = New-Object Windows.Forms.Form
    $script:StaffForm.Text = '무드월 직원 설정·점검'
    $script:StaffForm.ClientSize = New-Object Drawing.Size(520, 300)
    $script:StaffForm.StartPosition = 'CenterScreen'
    $script:StaffForm.FormBorderStyle = 'FixedDialog'
    $script:StaffForm.MaximizeBox = $false
    $label = New-Object Windows.Forms.Label
    $label.Location = New-Object Drawing.Point(20, 18)
    $label.Size = New-Object Drawing.Size(480, 102)
    $label.Text = "전광판 패널을 끈 상태에서 사용해 주세요.`r`n복제 화면에서는 이 직원 창도 전광판에 보입니다.`r`n설정 후 브라우저를 닫고 전광판 시작 아이콘을 다시 여세요.`r`n기존 등록·말풍선 기록·관리자 일시정지는 유지합니다."
    $script:StaffForm.Controls.Add($label)
    $setupButton = New-Object Windows.Forms.Button
    $setupButton.Text = '등록·설정 열기'
    $setupButton.Location = New-Object Drawing.Point(20, 130)
    $setupButton.Size = New-Object Drawing.Size(230, 44)
    $setupButton.Add_Click({ Open-StaffWindow Setup })
    $script:StaffForm.Controls.Add($setupButton)
    $reopenButton = New-Object Windows.Forms.Button
    $reopenButton.Text = '전광판 새로 열기'
    $reopenButton.Location = New-Object Drawing.Point(270, 130)
    $reopenButton.Size = New-Object Drawing.Size(230, 44)
    $reopenButton.Add_Click({ Open-StaffWindow Reopen })
    $script:StaffForm.Controls.Add($reopenButton)
    $diagnosticButton = New-Object Windows.Forms.Button
    $diagnosticButton.Text = '연결·진단 확인'
    $diagnosticButton.Location = New-Object Drawing.Point(20, 190)
    $diagnosticButton.Size = New-Object Drawing.Size(230, 44)
    $diagnosticButton.Add_Click({
        try {
            $connected = Wait-MoodwallEndpoint -DisplayUrl $script:Plan.DisplayUrl
            $status = if ($connected) { '웹페이지 응답 있음' } else { '웹페이지 응답 없음: 인터넷·서버 상태를 확인해 주세요.' }
            $events = @(Get-MoodwallLauncherEvents $PSScriptRoot)
            $recent = if ($events.Count) { $events -join "`r`n" } else { '최근 실행 기록 없음' }
            Show-StaffMessage ("$status`r`n`r`n등록 만료·해지는 관리자에게 새 코드를 요청한 뒤 [등록·설정 열기]에서 처리하세요.`r`n실제 HDMI 출력·기기 전원·게시 준비 여부는 이 검사로 확인할 수 없어요.`r`n`r`n최근 실행 기록 (UTC):`r`n$recent")
        } catch { Show-StaffMessage '진단 기록을 읽지 못했어요. 기기 담당자에게 확인을 요청해 주세요.' }
    })
    $script:StaffForm.Controls.Add($diagnosticButton)
    $closeButton = New-Object Windows.Forms.Button
    $closeButton.Text = '닫기'
    $closeButton.Location = New-Object Drawing.Point(270, 190)
    $closeButton.Size = New-Object Drawing.Size(230, 44)
    $closeButton.Add_Click({ $script:StaffForm.Close() })
    $script:StaffForm.Controls.Add($closeButton)
    [void]$script:StaffForm.ShowDialog()
} catch {
    if ($PlanOnly) { Write-Error $_.Exception.Message; exit 1 }
    Add-Type -AssemblyName System.Windows.Forms
    Show-StaffMessage '직원 설정을 열지 못했어요. 개발자에게 실제 서비스 주소와 런처 설치 상태 확인을 요청해 주세요.'
    exit 1
}
