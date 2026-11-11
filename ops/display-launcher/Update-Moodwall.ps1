[CmdletBinding()]
param(
    # Skip the "update now?" question (used when a person runs this from PowerShell).
    [switch]$Yes
)

# "무드월 업데이트" for the library laptop. One click brings the laptop to the team's
# latest main: close the wall, fast-forward the repository, rebuild only what changed
# (screen, server, DB migration), refresh these launcher scripts and icons, then turn
# the wall back on. A failure before a DB migration restores the previous version.
# The first run (or after a manual pull) rebuilds everything, because nothing records
# which commit the running screen and server were built from.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1')) -Force -DisableNameChecking
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

# Run from the installed launcher folder (desktop icon) or straight from the repository.
$installRoot = $PSScriptRoot
if (-not [IO.File]::Exists([IO.Path]::Combine($PSScriptRoot, 'config.json'))) {
    $installRoot = [IO.Path]::Combine($env:LOCALAPPDATA, 'Moodwall', 'DisplayLauncher')
}

$script:Form = $null
$script:Status = $null
$script:LogPath = $null

function Show-UpdateCard {
    $form = New-Object Windows.Forms.Form
    $form.Text = '무드월 업데이트'; $form.StartPosition = 'CenterScreen'; $form.TopMost = $true
    $form.Width = 720; $form.Height = 300; $form.FormBorderStyle = 'FixedDialog'; $form.ControlBox = $false
    $form.BackColor = [Drawing.Color]::FromArgb(242, 239, 232)
    $status = New-Object Windows.Forms.Label
    $status.AutoSize = $false; $status.Dock = 'Fill'; $status.TextAlign = 'MiddleCenter'; $status.Padding = 16
    $status.ForeColor = [Drawing.Color]::FromArgb(53, 58, 50)
    $status.Font = New-Object Drawing.Font('Malgun Gothic', [single]20, [Drawing.FontStyle]::Regular, [Drawing.GraphicsUnit]::Pixel)
    $form.Controls.Add($status)
    $script:Form = $form; $script:Status = $status
    $form.Show(); [Windows.Forms.Application]::DoEvents()
}

function Set-UpdateStatus {
    param([string]$Text)
    if ($script:Status) { $script:Status.Text = $Text; [Windows.Forms.Application]::DoEvents() }
    Write-UpdateLog ('== ' + $Text)
}

function Write-UpdateLog {
    param([string]$Text)
    if (-not $script:LogPath) { return }
    try { [IO.File]::AppendAllText($script:LogPath, $Text + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false))) } catch { }
}

function Show-UpdateMessage {
    param([string]$Text, [Windows.Forms.MessageBoxIcon]$Icon = [Windows.Forms.MessageBoxIcon]::Information)
    if ($script:Form) { $script:Form.Hide() }
    [void][Windows.Forms.MessageBox]::Show($Text, '무드월 업데이트', [Windows.Forms.MessageBoxButtons]::OK, $Icon)
}

function Resolve-Tool {
    param([string]$Name, [string]$Missing)
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { throw $Missing }
    return $command.Source
}

function Invoke-Tool {
    # Runs a program hidden, keeps the window responsive, logs output (never shown to staff).
    param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSeconds = 900, [string]$WorkingDirectory)
    $quoted = $Arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ } }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath; $info.Arguments = ($quoted -join ' ')
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    if ($WorkingDirectory) { $info.WorkingDirectory = $WorkingDirectory }
    Write-UpdateLog ('$ ' + [IO.Path]::GetFileName($FilePath) + ' ' + $info.Arguments)
    $process = [Diagnostics.Process]::Start($info)
    $out = $process.StandardOutput.ReadToEndAsync(); $err = $process.StandardError.ReadToEndAsync()
    $until = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while (-not $process.WaitForExit(200)) {
        [Windows.Forms.Application]::DoEvents()
        if ([DateTime]::UtcNow -gt $until) { try { $process.Kill() } catch { }; throw ('시간 초과: ' + [IO.Path]::GetFileName($FilePath)) }
    }
    $process.WaitForExit()
    Write-UpdateLog $out.Result; Write-UpdateLog $err.Result
    return [pscustomobject]@{ Code = $process.ExitCode; Out = $out.Result.Trim() }
}

function Invoke-Checked {
    param([string]$FilePath, [string[]]$Arguments, [string]$Failure, [int]$TimeoutSeconds = 900, [string]$WorkingDirectory)
    $result = Invoke-Tool -FilePath $FilePath -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds -WorkingDirectory $WorkingDirectory
    if ($result.Code -ne 0) { throw $Failure }
    return $result.Out
}

function Invoke-Git { param([string[]]$Arguments, [string]$Failure) Invoke-Checked $script:Git (@('-C', $script:Root) + $Arguments) $Failure 300 }

function Invoke-Npm {
    param([string[]]$Arguments)
    # npm is a .cmd script on Windows; run it through cmd.exe.
    Invoke-Checked $env:ComSpec (@('/d', '/c', 'npm.cmd', '--prefix', 'frontend') + $Arguments) '화면 빌드에 실패했어요.' 900 $script:Root | Out-Null
}

function Build-Frontend {
    Invoke-Npm @('ci', '--no-audit', '--no-fund')
    Invoke-Npm @('run', 'build')
}

function Start-DockerEngine {
    $docker = Resolve-MoodwallDocker
    if ((Invoke-Tool $docker @('info', '--format', '{{.ServerVersion}}') 20).Code -eq 0) { return $docker }
    $desktop = [IO.Path]::Combine($env:ProgramFiles, 'Docker', 'Docker', 'Docker Desktop.exe')
    if ([IO.File]::Exists($desktop)) { Start-Process -FilePath $desktop -WindowStyle Minimized | Out-Null }
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Seconds 4; [Windows.Forms.Application]::DoEvents()
        if ((Invoke-Tool $docker @('info', '--format', '{{.ServerVersion}}') 20).Code -eq 0) { return $docker }
    }
    throw 'Docker가 켜지지 않았어요.'
}

function Build-Server {
    param([switch]$Migrate)
    $docker = Start-DockerEngine
    Invoke-Checked $docker (Get-MoodwallComposeArguments $script:Server @('build', 'api', 'worker', 'migrate')) '서버 빌드에 실패했어요.' 1800 | Out-Null
    if ($Migrate) {
        $script:Migrated = $true
        Invoke-Checked $docker (Get-MoodwallComposeArguments $script:Server @('run', '--rm', 'migrate')) 'DB 업데이트에 실패했어요.' 600 | Out-Null
    }
    Invoke-Checked $docker (Get-MoodwallComposeArguments $script:Server @('up', '-d', '--no-build', 'api', 'worker')) '서버를 다시 켜지 못했어요.' 300 | Out-Null
}

function Close-Wall {
    # Same as "무드월 끄기" for the browser: only the dedicated Moodwall profile is closed.
    $plan = Get-MoodwallLaunchPlan -DisplayUrl $script:Config.displayUrl -InstallRoot $installRoot -DesktopDirectory ([Environment]::GetFolderPath('DesktopDirectory'))
    $owned = @(Select-MoodwallProfileProcesses -ProfileDirectory $plan.ProfileDirectory -BrowserPath $plan.BrowserPath -Processes @(Get-MoodwallBrowserProcesses $plan.BrowserPath))
    foreach ($entry in $owned) { try { [void][Diagnostics.Process]::GetProcessById([int]$entry.Id).CloseMainWindow() } catch { } }
    Start-Sleep -Seconds 3
    foreach ($entry in $owned) { try { Stop-Process -Id ([int]$entry.Id) -Force -ErrorAction Stop } catch { } }
}

function Update-Launcher {
    # Re-run this repository's installer so new scripts and icons replace the installed ones.
    $installer = [IO.Path]::Combine($script:Root, 'ops', 'display-launcher', 'Install.ps1')
    $powershell = [IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    $arguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installer, '-ServerRoot', $script:Root,
        '-ComposeFiles', (@($script:Server.composeFiles) -join ','))
    $startup = [IO.Path]::Combine([Environment]::GetFolderPath('Startup'), '무드월 켜기.lnk')
    if ([IO.File]::Exists($startup)) { $arguments += '-AutoStart' }
    Invoke-Checked $powershell $arguments '바탕화면 아이콘을 갱신하지 못했어요.' 300 | Out-Null
}

$script:Migrated = $false
$moved = $false
$old = $null
$plan = $null
$state = $null
try {
    $script:Config = Read-MoodwallConfiguration -InstallRoot $installRoot
    $script:Server = Read-MoodwallServerConfiguration -InstallRoot $installRoot
    if (-not $script:Server) { throw '서버 설정(server.json)이 없어요. 먼저 런처를 -ServerRoot로 설치해 주세요.' }
    $script:Root = $script:Server.serverRoot
    $logRoot = [IO.Path]::Combine($installRoot, 'Logs')
    [void][IO.Directory]::CreateDirectory($logRoot)
    $script:LogPath = [IO.Path]::Combine($logRoot, 'update.log')
    [IO.File]::WriteAllText($script:LogPath, ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ') + ' update' + [Environment]::NewLine))

    if (-not $Yes) {
        $answer = [Windows.Forms.MessageBox]::Show("무드월을 최신 버전으로 업데이트할까요?`r`n업데이트하는 동안(보통 3~10분) 전광판이 꺼졌다가 끝나면 다시 켜져요.",
            '무드월 업데이트', [Windows.Forms.MessageBoxButtons]::YesNo, [Windows.Forms.MessageBoxIcon]::Question)
        if ($answer -ne [Windows.Forms.DialogResult]::Yes) { exit 0 }
    }
    Show-UpdateCard
    Write-MoodwallLauncherEvent $installRoot UPDATE_STARTED
    $script:Git = Resolve-Tool 'git.exe' 'Git이 설치되어 있지 않아요.'
    Resolve-Tool 'npm.cmd' 'Node.js(npm)가 설치되어 있지 않아요.' | Out-Null

    Set-UpdateStatus '새 버전이 있는지 확인하고 있어요.'
    if ((Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD') '저장소를 읽지 못했어요.') -cne 'main') {
        throw '저장소가 main 브랜치가 아니에요. 담당자에게 알려 주세요.'
    }
    if (Invoke-Git @('status', '--porcelain', '--untracked-files=no') '저장소를 읽지 못했어요.') {
        throw '노트북에서 고친 파일이 있어 업데이트하지 않았어요. 담당자에게 알려 주세요.'
    }
    Invoke-Git @('fetch', '--quiet', 'origin', 'main') '팀 저장소에 연결하지 못했어요. 인터넷 연결을 확인해 주세요.' | Out-Null
    $old = Invoke-Git @('rev-parse', 'HEAD') '저장소를 읽지 못했어요.'
    $new = Invoke-Git @('rev-parse', 'FETCH_HEAD') '저장소를 읽지 못했어요.'
    $changed = @()
    if ($old -cne $new) {
        if ((Invoke-Tool $script:Git @('-C', $script:Root, 'merge-base', '--is-ancestor', $old, $new)).Code -ne 0) {
            throw '노트북의 저장소가 팀 저장소와 달라 자동으로 업데이트할 수 없어요. 담당자에게 알려 주세요.'
        }
        $changed = @((Invoke-Git @('diff', '--name-only', $old, $new) '변경 목록을 읽지 못했어요.') -split "`r?`n" | Where-Object { $_ })
    }
    $state = Read-MoodwallUpdateState -InstallRoot $installRoot
    $plan = Get-MoodwallUpdatePlan -ChangedFiles $changed -PreviousCommit $old -State $state
    $launcherMissing = -not [IO.File]::Exists([IO.Path]::Combine($installRoot, 'Update-Moodwall.ps1'))
    $migrate = $plan.RebuildServer
    if ($old -ceq $new -and -not $plan.BuildFrontend -and -not $plan.RebuildServer -and -not $launcherMissing) {
        Write-MoodwallLauncherEvent $installRoot UPDATE_NONE
        Show-UpdateMessage '이미 최신 버전이에요. 바꿀 것이 없어요.'
        exit 0
    }

    Set-UpdateStatus '전광판을 잠시 끄고 있어요.'
    Close-Wall
    if ($old -cne $new) {
        Set-UpdateStatus '새 버전을 받고 있어요.'
        Invoke-Git @('merge', '--ff-only', '--quiet', $new) '새 버전을 받지 못했어요.' | Out-Null
        $moved = $true
    }
    $frontendCommit = if ($state) { [string]$state.frontendCommit } else { '' }
    $backendCommit = if ($state) { [string]$state.backendCommit } else { '' }
    if ($plan.BuildFrontend) {
        Set-UpdateStatus '전광판 화면을 새로 만들고 있어요. (2~5분)'
        Build-Frontend
        $frontendCommit = $new
        Write-MoodwallUpdateState $installRoot $frontendCommit $backendCommit
    }
    if ($plan.RebuildServer) {
        Set-UpdateStatus '서버를 새로 만들고 있어요. (3~10분)'
        Build-Server -Migrate:$migrate
        $backendCommit = $new
        Write-MoodwallUpdateState $installRoot $frontendCommit $backendCommit
    }
    if ($plan.RefreshLauncher -or $launcherMissing) {
        Set-UpdateStatus '바탕화면 아이콘을 갱신하고 있어요.'
        Update-Launcher
    }
    Write-MoodwallLauncherEvent $installRoot UPDATE_DONE
    if ($script:Form) { $script:Form.Close(); $script:Form = $null }
    # Turn the wall back on with the (possibly refreshed) installed "무드월 켜기".
    & ([IO.Path]::Combine($installRoot, 'Start-Moodwall.ps1'))
    exit 0
} catch {
    $reason = $_.Exception.Message
    Write-UpdateLog ('!! ' + $reason)
    Write-MoodwallLauncherEvent $installRoot UPDATE_FAILED
    $restored = $false
    if ($moved -and -not $script:Migrated -and $old) {
        # Before any DB migration the previous version can be rebuilt exactly.
        try {
            Set-UpdateStatus '문제가 생겨 이전 버전으로 되돌리고 있어요.'
            Invoke-Git @('reset', '--hard', '--quiet', $old) '이전 버전으로 되돌리지 못했어요.' | Out-Null
            Build-Frontend
            if ($plan -and $plan.RebuildServer) { Build-Server }
            Write-MoodwallUpdateState $installRoot $old $(if ($plan -and $plan.RebuildServer) { $old } elseif ($state) { [string]$state.backendCommit } else { '' })
            Write-MoodwallLauncherEvent $installRoot UPDATE_ROLLED_BACK
            $restored = $true
        } catch { Write-UpdateLog ('!! rollback: ' + $_.Exception.Message) }
    }
    $tail = if ($restored) { "`r`n이전 버전으로 되돌렸어요. '무드월 켜기'로 평소처럼 쓰면 돼요." }
            elseif ($moved -or $script:Migrated) { "`r`n전광판이 켜지지 않을 수 있어요. 담당자에게 바로 알려 주세요." }
            else { "`r`n아무것도 바꾸지 않았어요. '무드월 켜기'로 평소처럼 쓰면 돼요." }
    Show-UpdateMessage ('업데이트하지 못했어요: ' + $reason + $tail + "`r`n(기록: " + $script:LogPath + ')') ([Windows.Forms.MessageBoxIcon]::Warning)
    exit 1
}
