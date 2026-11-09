[CmdletBinding()]
param()

# "무드월 켜기" for library staff. The laptop is the server: start Docker Desktop if
# needed, start the Moodwall containers (DB, API, worker, tunnel), wait until the
# public HTTPS address answers, then open the wall full screen on the LED panel.
# Staff only double-click; every wait is shown on a full-screen status card.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1')) -Force -DisableNameChecking
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

$script:Form = $null
$script:Status = $null

function Show-PowerOnCard {
    param([object]$Screen)
    $form = New-Object Windows.Forms.Form
    $form.FormBorderStyle = 'None'; $form.StartPosition = 'Manual'; $form.TopMost = $true; $form.ShowInTaskbar = $true
    $form.Text = '무드월 켜기'
    $form.BackColor = [Drawing.Color]::FromArgb(242, 239, 232)
    if ($Screen) { $form.Bounds = New-Object Drawing.Rectangle([int]$Screen.X, [int]$Screen.Y, [int]$Screen.Width, [int]$Screen.Height) }
    else { $form.WindowState = 'Maximized' }
    $height = [Math]::Max(300, $form.Height)
    $brand = New-Object Windows.Forms.Label
    $brand.Text = 'MOOD WALL'; $brand.AutoSize = $false; $brand.TextAlign = 'MiddleCenter'; $brand.Dock = 'Top'
    $brand.Height = [int]($height * 0.45); $brand.ForeColor = [Drawing.Color]::FromArgb(53, 58, 50)
    $brand.Font = New-Object Drawing.Font('Malgun Gothic', [single]([Math]::Max(28, $height * 0.11)), [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
    $status = New-Object Windows.Forms.Label
    $status.AutoSize = $false; $status.TextAlign = 'TopCenter'; $status.Dock = 'Fill'
    $status.ForeColor = [Drawing.Color]::FromArgb(110, 115, 100)
    $status.Font = New-Object Drawing.Font('Malgun Gothic', [single]([Math]::Max(16, $height * 0.045)), [Drawing.FontStyle]::Regular, [Drawing.GraphicsUnit]::Pixel)
    $form.Controls.Add($status); $form.Controls.Add($brand)
    $script:Form = $form; $script:Status = $status
    $form.Show(); [Windows.Forms.Application]::DoEvents()
}

function Set-PowerOnStatus {
    param([string]$Text)
    if ($script:Status) { $script:Status.Text = $Text; [Windows.Forms.Application]::DoEvents() }
}

function Wait-Responsive {
    param([int]$Seconds)
    $until = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $until) { [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 200 }
}

function Test-DockerEngine {
    param([string]$Docker)
    $process = Start-Process -FilePath $Docker -ArgumentList 'info --format "{{.ServerVersion}}"' -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(15000)) { try { $process.Kill() } catch { }; return $false }
    return $process.ExitCode -eq 0
}

function Invoke-Docker {
    param([string]$Docker, [string[]]$Arguments, [int]$TimeoutSeconds)
    $quoted = $Arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ } }
    $process = Start-Process -FilePath $Docker -ArgumentList ($quoted -join ' ') -WindowStyle Hidden -PassThru
    $until = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while (-not $process.HasExited -and [DateTime]::UtcNow -lt $until) { Wait-Responsive 1 }
    if (-not $process.HasExited) { try { $process.Kill() } catch { }; return $false }
    return $process.ExitCode -eq 0
}

try {
    $config = Read-MoodwallConfiguration -InstallRoot $PSScriptRoot
    $server = Read-MoodwallServerConfiguration -InstallRoot $PSScriptRoot
    if (-not $server) { throw 'server.json is missing' }
    $screen = $null
    try { $screen = Select-MoodwallTargetScreen @(Get-MoodwallScreens) } catch { }
    Show-PowerOnCard $screen
    Write-MoodwallLauncherEvent $PSScriptRoot POWER_ON

    Set-PowerOnStatus '무드월을 켜고 있어요. 1~3분 정도 걸려요. 이 화면을 그대로 두세요.'
    $docker = Resolve-MoodwallDocker
    if (-not (Test-DockerEngine $docker)) {
        Write-MoodwallLauncherEvent $PSScriptRoot DOCKER_STARTING
        $desktop = [IO.Path]::Combine($env:ProgramFiles, 'Docker', 'Docker', 'Docker Desktop.exe')
        if ([IO.File]::Exists($desktop)) { Start-Process -FilePath $desktop -WindowStyle Minimized | Out-Null }
        $ready = $false
        for ($attempt = 0; $attempt -lt 60 -and -not $ready; $attempt++) { Wait-Responsive 4; $ready = Test-DockerEngine $docker }
        if (-not $ready) { Write-MoodwallLauncherEvent $PSScriptRoot DOCKER_UNAVAILABLE; throw 'Docker engine did not start' }
    }

    Set-PowerOnStatus '서버를 준비하고 있어요. 잠시만 기다려 주세요.'
    # Containers were stopped by "무드월 끄기"; restart policies alone would not bring them back.
    if (-not (Invoke-Docker $docker (Get-MoodwallComposeArguments $server @('up', '-d', '--no-build', 'api', 'worker', 'cloudflared')) 300)) {
        Write-MoodwallLauncherEvent $PSScriptRoot SERVER_FAILED; throw 'docker compose up failed'
    }

    Set-PowerOnStatus '인터넷 주소에 연결하고 있어요.'
    $ready = $false
    for ($attempt = 0; $attempt -lt 60 -and -not $ready; $attempt++) {
        $ready = Test-MoodwallEndpoint -DisplayUrl $config.displayUrl
        if (-not $ready) { Wait-Responsive 3 }
    }
    if (-not $ready) { Write-MoodwallLauncherEvent $PSScriptRoot SERVER_UNAVAILABLE; throw 'public address unavailable' }

    $script:Form.Close(); $script:Form = $null
    & ([IO.Path]::Combine($PSScriptRoot, 'Start-Display.ps1'))
    exit 0
} catch {
    Write-MoodwallLauncherEvent $PSScriptRoot POWER_ON_FAILED
    try {
        if (-not $script:Form) { Show-PowerOnCard $null }
        Set-PowerOnStatus ("무드월을 켜지 못했어요.`r`n인터넷 연결을 확인하고, 노트북을 다시 시작한 뒤 '무드월 켜기'를 한 번 더 눌러 주세요.`r`n그래도 안 되면 담당 선생님께 알려 주세요.")
        $close = New-Object Windows.Forms.Button
        $close.Text = '닫기'; $close.Dock = 'Bottom'; $close.Height = 70
        $close.Font = New-Object Drawing.Font('Malgun Gothic', [single]22, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
        $close.Add_Click({ $script:Form.Close() })
        $script:Form.Controls.Add($close)
        $script:Form.Hide(); [void]$script:Form.ShowDialog()
    } catch { }
    exit 1
}
