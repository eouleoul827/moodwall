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
    Assert-MoodwallLocalPath -Path $plan.ShortcutPath
    # The installer changes only this owned directory and this current-user shortcut.
    # Browser profiles survive reinstall; no browser, registry or startup setting is changed.
    $shell = New-Object -ComObject WScript.Shell
    if ([IO.File]::Exists($plan.ShortcutPath)) {
        $old = $shell.CreateShortcut($plan.ShortcutPath)
        if ($old.TargetPath -ne $plan.ShortcutTarget -or $old.Arguments -ne $plan.ShortcutArguments) {
            throw '같은 이름의 다른 바로가기가 있어 덮어쓰지 않았습니다. 개발자에게 확인을 요청해 주세요.'
        }
    }
    [void][IO.Directory]::CreateDirectory($plan.InstallRoot)
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($plan.ShortcutPath))
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText([IO.Path]::Combine($plan.InstallRoot, 'moodwall-display-launcher.marker'), 'MoodwallDisplayLauncher:1', $encoding)
    foreach ($file in @('DisplayLauncher.psm1', 'Start-Display.ps1')) {
        Copy-Item -LiteralPath ([IO.Path]::Combine($PSScriptRoot, $file)) -Destination ([IO.Path]::Combine($plan.InstallRoot, $file)) -Force
    }
    [IO.File]::WriteAllText([IO.Path]::Combine($plan.InstallRoot, 'config.json'), ($plan.Configuration | ConvertTo-Json), $encoding)
    $shortcut = $shell.CreateShortcut($plan.ShortcutPath)
    $shortcut.TargetPath = $plan.ShortcutTarget
    $shortcut.Arguments = $plan.ShortcutArguments
    $shortcut.WorkingDirectory = $plan.InstallRoot
    $shortcut.Description = '무드월 전광판을 전체 화면으로 엽니다. 종료: Alt+F4'
    $shortcut.IconLocation = $plan.BrowserPath + ',0'
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Write-Output ('설치 완료: ' + $plan.ShortcutPath)
    Write-Output '직원은 이 아이콘만 실행하면 됩니다. 최초 등록과 30일 만료 시에는 관리자 등록 코드가 필요합니다.'
} catch {
    Write-Error ('설치하지 못했습니다: ' + $_.Exception.Message)
    exit 1
}
