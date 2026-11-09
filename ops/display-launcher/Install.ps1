[CmdletBinding()]
param(
    [string]$DisplayUrl,
    [Alias('ValidateOnly', 'DryRun')][switch]$PlanOnly,
    [string]$InstallRoot = [IO.Path]::Combine($env:LOCALAPPDATA, 'Moodwall', 'DisplayLauncher'),
    [string]$DesktopDirectory = [Environment]::GetFolderPath('DesktopDirectory'),
    # Library laptop as the server: the repository folder holding .env and compose files.
    [string]$ServerRoot,
    [string[]]$ComposeFiles = @('compose.yaml', 'compose.frontend.yaml', 'compose.gemini.yaml', 'compose.library.yaml'),
    # Also start Moodwall right after the staff Windows login.
    [switch]$AutoStart,
    [string]$StartupDirectory = [Environment]::GetFolderPath('Startup')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1')) -Force -DisableNameChecking

try {
    $server = $null
    if ($ServerRoot) {
        $server = Get-MoodwallServerConfiguration -ServerRoot $ServerRoot -ComposeFiles $ComposeFiles
        # One address everywhere: the wall must be opened at the server's PUBLIC_ORIGIN,
        # otherwise every POST fails the origin check and the wall stays blank.
        $envOrigin = Get-MoodwallEnvOrigin ([IO.Path]::Combine($server.serverRoot, '.env'))
        $expected = ConvertTo-MoodwallDisplayUrl $envOrigin
        if (([Uri]$expected).Scheme -ne 'https') { throw '.env의 MOODWALL_PUBLIC_ORIGIN을 학생 휴대폰이 접속할 HTTPS 공개 주소로 먼저 설정해 주세요.' }
        if (-not $DisplayUrl) { $DisplayUrl = $envOrigin }
        if ((ConvertTo-MoodwallDisplayUrl $DisplayUrl) -ne $expected) { throw ('전광판 주소가 .env의 MOODWALL_PUBLIC_ORIGIN(' + $envOrigin + ')과 다릅니다.') }
        if (-not [IO.File]::Exists([IO.Path]::Combine($server.serverRoot, 'frontend', 'dist', 'index.html'))) {
            throw 'frontend/dist가 없습니다. 먼저 npm --prefix frontend ci 와 npm --prefix frontend run build를 실행해 주세요.'
        }
    } elseif (-not $DisplayUrl) { throw '-DisplayUrl 또는 -ServerRoot가 필요합니다.' }
    $plan = Get-MoodwallLaunchPlan -DisplayUrl $DisplayUrl -InstallRoot $InstallRoot -DesktopDirectory $DesktopDirectory
    Assert-MoodwallInstallRoot -InstallRoot $plan.InstallRoot
    if ($PlanOnly) {
        $plan | Add-Member -NotePropertyName Server -NotePropertyValue $server
        $plan | ConvertTo-Json -Depth 5
        exit 0
    }
    Assert-MoodwallDeploymentUrl -DisplayUrl $plan.DisplayUrl
    $staffShortcut = [pscustomobject]@{ Path = $plan.StaffShortcutPath; Arguments = $plan.StaffShortcutArguments; Description = '패널을 끈 상태에서 등록·설정·진단을 엽니다.' }
    if ($server) {
        # Library staff see exactly two everyday icons: on and off.
        $startShortcut = [pscustomobject]@{ Path = $plan.PowerOnShortcutPath; Arguments = $plan.PowerOnArguments; Description = '무드월 서버와 전광판 화면을 켭니다.' }
        $shortcuts = @($startShortcut,
            [pscustomobject]@{ Path = $plan.PowerOffShortcutPath; Arguments = $plan.PowerOffArguments; Description = '무드월을 끕니다. 그다음 노트북을 꺼도 됩니다.' },
            $staffShortcut)
    } else {
        $startShortcut = [pscustomobject]@{ Path = $plan.ShortcutPath; Arguments = $plan.ShortcutArguments; Description = '무드월 전광판을 전체 화면으로 엽니다. 종료: Alt+F4' }
        $shortcuts = @($startShortcut, $staffShortcut)
    }
    if ($AutoStart) {
        $shortcuts += [pscustomobject]@{ Path = [IO.Path]::Combine((ConvertTo-MoodwallAbsolutePath $StartupDirectory), [IO.Path]::GetFileName($startShortcut.Path)); Arguments = $startShortcut.Arguments; Description = $startShortcut.Description }
    }
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
    foreach ($entry in $shortcuts) { [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($entry.Path)) }
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText([IO.Path]::Combine($plan.InstallRoot, 'moodwall-display-launcher.marker'), 'MoodwallDisplayLauncher:1', $encoding)
    foreach ($file in @('DisplayLauncher.psm1', 'Start-Display.ps1', 'Start-Staff.ps1', 'Start-Moodwall.ps1', 'Stop-Moodwall.ps1')) {
        Copy-Item -LiteralPath ([IO.Path]::Combine($PSScriptRoot, $file)) -Destination ([IO.Path]::Combine($plan.InstallRoot, $file)) -Force
    }
    [IO.File]::WriteAllText([IO.Path]::Combine($plan.InstallRoot, 'config.json'), ($plan.Configuration | ConvertTo-Json), $encoding)
    if ($server) { [IO.File]::WriteAllText([IO.Path]::Combine($plan.InstallRoot, 'server.json'), ($server | ConvertTo-Json), $encoding) }
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
    foreach ($entry in $shortcuts) { Write-Output ('설치 완료: ' + $entry.Path) }
    if ($server) { Write-Output '직원은 매일 [무드월 켜기]와 [무드월 끄기]만 사용합니다. 최초 등록·만료 복구는 직원 설정·점검을 사용해 주세요.' }
    else { Write-Output '평소에는 전광판 시작 아이콘을 사용합니다. 최초 등록·만료 복구는 패널을 끈 상태에서 직원 설정·점검을 사용해 주세요.' }
} catch {
    Write-Error ('설치하지 못했습니다: ' + $_.Exception.Message)
    exit 1
}
