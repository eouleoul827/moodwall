[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DisplayUrl,
    [Alias('ValidateOnly', 'DryRun')][switch]$PlanOnly,
    [string]$InstallRoot = [IO.Path]::Combine($env:LOCALAPPDATA, 'Moodwall', 'DisplayLauncher'),
    [string]$DesktopDirectory = [Environment]::GetFolderPath('DesktopDirectory')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1')) -Force -DisableNameChecking

try {
    $plan = Get-MoodwallLaunchPlan -DisplayUrl $DisplayUrl -InstallRoot $InstallRoot -DesktopDirectory $DesktopDirectory
    Assert-MoodwallInstallRoot -InstallRoot $plan.InstallRoot
    if ($PlanOnly) {
        $plan | ConvertTo-Json -Depth 5
        exit 0
    }
    Assert-MoodwallDeploymentUrl -DisplayUrl $plan.DisplayUrl
    $shortcuts = @(
        [pscustomobject]@{ Path = $plan.ShortcutPath; Arguments = $plan.ShortcutArguments; Description = '무드월 전광판을 전체 화면으로 엽니다. 종료: Alt+F4' },
        [pscustomobject]@{ Path = $plan.StaffShortcutPath; Arguments = $plan.StaffShortcutArguments; Description = '패널을 끈 상태에서 등록·설정·진단을 엽니다.' }
    )
    # Validate both destinations before writing anything; the legacy public shortcut arguments stay identical.
    foreach ($entry in $shortcuts) { Assert-MoodwallLocalPath -Path $entry.Path }
    # The installer changes only this owned directory and these current-user shortcuts.
    # Browser profiles survive reinstall; no browser, registry or startup setting is changed.
    $shell = New-Object -ComObject WScript.Shell
    foreach ($entry in $shortcuts) {
        if ([IO.File]::Exists($entry.Path)) {
            $old = $shell.CreateShortcut($entry.Path)
            if ($old.TargetPath -ne $plan.ShortcutTarget -or $old.Arguments -ne $entry.Arguments) {
                throw '같은 이름의 다른 바로가기가 있어 덮어쓰지 않았습니다. 개발자에게 확인을 요청해 주세요.'
            }
        }
    }
    [void][IO.Directory]::CreateDirectory($plan.InstallRoot)
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($plan.ShortcutPath))
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText([IO.Path]::Combine($plan.InstallRoot, 'moodwall-display-launcher.marker'), 'MoodwallDisplayLauncher:1', $encoding)
    foreach ($file in @('DisplayLauncher.psm1', 'Start-Display.ps1', 'Start-Staff.ps1')) {
        Copy-Item -LiteralPath ([IO.Path]::Combine($PSScriptRoot, $file)) -Destination ([IO.Path]::Combine($plan.InstallRoot, $file)) -Force
    }
    [IO.File]::WriteAllText([IO.Path]::Combine($plan.InstallRoot, 'config.json'), ($plan.Configuration | ConvertTo-Json), $encoding)
    foreach ($entry in $shortcuts) {
        $shortcut = $shell.CreateShortcut($entry.Path)
        $shortcut.TargetPath = $plan.ShortcutTarget
        $shortcut.Arguments = $entry.Arguments
        $shortcut.WorkingDirectory = $plan.InstallRoot
        $shortcut.Description = $entry.Description
        $shortcut.IconLocation = $plan.BrowserPath + ',0'
        $shortcut.WindowStyle = 7
        $shortcut.Save()
    }
    Write-Output ('설치 완료: ' + $plan.ShortcutPath)
    Write-Output ('직원 설정·점검: ' + $plan.StaffShortcutPath)
    Write-Output '평소에는 전광판 시작 아이콘을 사용합니다. 최초 등록·만료 복구는 패널을 끈 상태에서 직원 설정·점검을 사용해 주세요.'
} catch {
    Write-Error ('설치하지 못했습니다: ' + $_.Exception.Message)
    exit 1
}
