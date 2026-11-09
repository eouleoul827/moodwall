Set-StrictMode -Version Latest

$script:MarkerName = 'moodwall-display-launcher.marker'
$script:MarkerValue = 'MoodwallDisplayLauncher:1'
$script:ShortcutName = '무드월 전광판 시작.lnk'
$script:StaffShortcutName = '무드월 직원 설정·점검.lnk'
$script:PowerOnShortcutName = '무드월 켜기.lnk'
$script:PowerOffShortcutName = '무드월 끄기.lnk'
# Library LED wall: 2046 x 768 px (8000 x 3000 mm, P4). Used to find it among screens.
$script:PanelAspect = 2046 / 768

function ConvertTo-MoodwallDisplayUrl {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$DisplayUrl)

    if ($DisplayUrl -match '[\s\\"''\x00-\x1f\x7f?#]') {
        throw '주소에는 공백, 역슬래시, 따옴표, 로그인 정보, 쿼리 또는 #을 넣을 수 없습니다.'
    }
    $parsed = $null
    if (-not [Uri]::TryCreate($DisplayUrl, [UriKind]::Absolute, [ref]$parsed)) {
        throw '완전한 서비스 웹 주소가 필요합니다.'
    }
    if ($parsed.UserInfo -or -not $parsed.Host -or $parsed.HostNameType -eq [UriHostNameType]::Unknown) {
        throw '로그인 정보가 없는 서비스 웹 주소를 입력해 주세요.'
    }
    $isLocal = $parsed.IsLoopback
    if ($parsed.Scheme -ne 'https' -and -not ($parsed.Scheme -eq 'http' -and $isLocal)) {
        throw '운영 주소는 HTTPS여야 합니다. HTTP는 localhost 개발 환경에서만 허용합니다.'
    }
    if ($parsed.AbsolutePath -notin @('/', '/display')) {
        throw '서비스 기본 주소 또는 /display 주소만 입력해 주세요.'
    }
    return $parsed.GetLeftPart([UriPartial]::Authority) + '/display'
}

function ConvertTo-MoodwallAbsolutePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ($Path -match '["\x00-\x1f\x7f]' -or $Path -notmatch '^[A-Za-z]:[\\/]') {
        throw '따옴표와 제어 문자가 없는 절대 경로가 필요합니다.'
    }
    $fullPath = [IO.Path]::GetFullPath($Path)
    if ($fullPath.StartsWith('\\')) { throw '설치와 프로필에는 로컬 디스크 경로를 사용해 주세요.' }
    if ($fullPath.Length -gt 3) { $fullPath = $fullPath.TrimEnd([IO.Path]::DirectorySeparatorChar) }
    return $fullPath
}

function Assert-MoodwallLocalPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $current = ConvertTo-MoodwallAbsolutePath $Path
    while ($current) {
        if (([IO.File]::Exists($current) -or [IO.Directory]::Exists($current)) -and
            (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw '설치와 프로필 경로에는 연결 디렉터리나 심볼릭 링크를 사용할 수 없습니다.'
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
}

function Assert-MoodwallDeploymentUrl {
    param([Parameter(Mandatory = $true)][string]$DisplayUrl)
    $url = [Uri](ConvertTo-MoodwallDisplayUrl $DisplayUrl)
    $hostName = $url.DnsSafeHost.TrimEnd('.').ToLowerInvariant()
    if ($hostName -match '(^|\.)(example\.(com|org|net)|example|invalid|test)$') {
        throw '예시 주소로는 설치할 수 없습니다. 실제 배포된 서비스 주소가 필요합니다.'
    }
}

function Get-MoodwallBrowserCandidates {
    $locations = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) | Where-Object { $_ }
    foreach ($kind in @('Edge', 'Chrome')) {
        $relative = if ($kind -eq 'Edge') { 'Microsoft\Edge\Application\msedge.exe' } else { 'Google\Chrome\Application\chrome.exe' }
        foreach ($location in $locations) {
            [pscustomobject]@{ Kind = $kind; Path = [IO.Path]::Combine($location, $relative) }
        }
    }
}

function Resolve-MoodwallBrowser {
    [CmdletBinding()]
    param([object[]]$Candidates = @(Get-MoodwallBrowserCandidates))
    foreach ($candidate in $Candidates) {
        if ($candidate.Kind -notin @('Edge', 'Chrome')) { continue }
        if ([IO.File]::Exists($candidate.Path)) {
            return [pscustomobject]@{ Kind = $candidate.Kind; Path = (ConvertTo-MoodwallAbsolutePath $candidate.Path) }
        }
    }
    throw 'Microsoft Edge 또는 Google Chrome을 설치한 뒤 다시 실행해 주세요.'
}

function Get-MoodwallLaunchPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$DisplayUrl,
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$DesktopDirectory,
        [object]$Browser = (Resolve-MoodwallBrowser)
    )
    $url = ConvertTo-MoodwallDisplayUrl $DisplayUrl
    $rootPath = ConvertTo-MoodwallAbsolutePath $InstallRoot
    Assert-MoodwallLocalPath $rootPath
    $desktopPath = ConvertTo-MoodwallAbsolutePath $DesktopDirectory
    if ($Browser.Kind -notin @('Edge', 'Chrome')) { throw '지원하지 않는 브라우저입니다.' }
    $browserPath = ConvertTo-MoodwallAbsolutePath $Browser.Path
    $profilePath = [IO.Path]::Combine($rootPath, 'Profiles', $Browser.Kind)
    $launcherPath = [IO.Path]::Combine($rootPath, 'Start-Display.ps1')
    $powershellPath = [IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    [pscustomobject]@{
        SchemaVersion = 1
        DisplayUrl = $url
        InstallRoot = $rootPath
        BrowserKind = $Browser.Kind
        BrowserPath = $browserPath
        ProfileDirectory = $profilePath
        BrowserArguments = @('--no-first-run', '--no-default-browser-check', '--hide-crash-restore-bubble', '--new-window', '--start-fullscreen', ('--user-data-dir="' + $profilePath + '"'), ('"' + $url + '"'))
        ShortcutPath = [IO.Path]::Combine($desktopPath, $script:ShortcutName)
        ShortcutTarget = $powershellPath
        ShortcutArguments = '-NoLogo -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launcherPath + '"'
        StaffShortcutPath = [IO.Path]::Combine($desktopPath, $script:StaffShortcutName)
        StaffShortcutArguments = '-NoLogo -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + [IO.Path]::Combine($rootPath, 'Start-Staff.ps1') + '"'
        SetupUrl = ([Uri]$url).GetLeftPart([UriPartial]::Authority) + '/display/setup'
        SetupBrowserArguments = @('--no-first-run', '--no-default-browser-check', '--new-window', ('--user-data-dir="' + $profilePath + '"'), ('"' + ([Uri]$url).GetLeftPart([UriPartial]::Authority) + '/display/setup"'))
        StaffSessionPath = [IO.Path]::Combine($rootPath, 'staff-session.marker')
        PowerOnShortcutPath = [IO.Path]::Combine($desktopPath, $script:PowerOnShortcutName)
        PowerOnArguments = '-NoLogo -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + [IO.Path]::Combine($rootPath, 'Start-Moodwall.ps1') + '"'
        PowerOffShortcutPath = [IO.Path]::Combine($desktopPath, $script:PowerOffShortcutName)
        PowerOffArguments = '-NoLogo -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + [IO.Path]::Combine($rootPath, 'Stop-Moodwall.ps1') + '"'
        Configuration = [ordered]@{ schemaVersion = 1; displayUrl = $url }
    }
}

function Assert-MoodwallInstallRoot {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$InstallRoot)
    $rootPath = ConvertTo-MoodwallAbsolutePath $InstallRoot
    if (-not [IO.Directory]::Exists($rootPath)) {
        if ([IO.File]::Exists($rootPath)) { throw '설치 위치에 같은 이름의 파일이 있습니다.' }
        return
    }
    if (([IO.File]::GetAttributes($rootPath) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw '설치 위치에는 바로가기나 연결 디렉터리를 사용할 수 없습니다.'
    }
    $items = @(Get-ChildItem -LiteralPath $rootPath -Force -ErrorAction Stop)
    if ($items.Count -eq 0) { return }
    $markerPath = [IO.Path]::Combine($rootPath, $script:MarkerName)
    if (-not [IO.File]::Exists($markerPath) -or [IO.File]::ReadAllText($markerPath).Trim() -ne $script:MarkerValue) {
        throw '기존 파일이 있는 폴더에는 설치할 수 없습니다. 무드월 전용 빈 폴더를 선택해 주세요.'
    }
    $allowed = @($script:MarkerName, 'DisplayLauncher.psm1', 'Start-Display.ps1', 'Start-Staff.ps1', 'Start-Moodwall.ps1', 'Stop-Moodwall.ps1', 'config.json', 'server.json', 'Profiles', 'Logs', 'staff-session.marker')
    foreach ($item in $items) {
        if ($item.Name -notin $allowed -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw '설치 폴더에 알 수 없는 파일 또는 연결 항목이 있어 갱신을 중단했습니다.'
        }
        if ($item.PSIsContainer -and $item.Name -notin @('Profiles', 'Logs')) { throw '설치 파일 위치에 폴더가 있습니다.' }
        if (-not $item.PSIsContainer -and $item.Name -in @('Profiles', 'Logs')) { throw '프로필 또는 진단 폴더 위치에 파일이 있습니다.' }
    }
}

function Read-MoodwallConfiguration {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$InstallRoot)
    $rootPath = ConvertTo-MoodwallAbsolutePath $InstallRoot
    $path = [IO.Path]::Combine($rootPath, 'config.json')
    $raw = [IO.File]::ReadAllText($path)
    if ($raw.Length -gt 4096) { throw '설정 파일을 확인해 주세요.' }
    $config = $raw | ConvertFrom-Json -ErrorAction Stop
    $keys = @($config.PSObject.Properties.Name)
    if ($keys.Count -ne 2 -or 'schemaVersion' -notin $keys -or 'displayUrl' -notin $keys -or $config.schemaVersion -ne 1 -or $config.displayUrl -isnot [string]) {
        throw '설정 파일 형식이 올바르지 않습니다. 개발자에게 재설치를 요청해 주세요.'
    }
    return [pscustomobject]@{ schemaVersion = 1; displayUrl = (ConvertTo-MoodwallDisplayUrl $config.displayUrl) }
}

function Get-MoodwallExistingDisplay {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProfileDirectory,
        [Parameter(Mandatory = $true)][string]$BrowserPath,
        [AllowEmptyCollection()][object[]]$Processes = @()
    )
    $matched = @(Select-MoodwallProfileProcesses -ProfileDirectory $ProfileDirectory -BrowserPath $BrowserPath -Processes $Processes)
    $window = @($matched | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1)
    if ($window.Count) { return [pscustomobject]@{ Action = 'Activate'; Process = $window[0] } }
    if ($matched.Count) { return [pscustomobject]@{ Action = 'AlreadyRunning'; Process = $matched[0] } }
    return [pscustomobject]@{ Action = 'Launch'; Process = $null }
}

function Select-MoodwallProfileProcesses {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProfileDirectory,
        [Parameter(Mandatory = $true)][string]$BrowserPath,
        [AllowEmptyCollection()][object[]]$Processes = @()
    )
    $profilePath = ConvertTo-MoodwallAbsolutePath $ProfileDirectory
    $expectedBrowser = ConvertTo-MoodwallAbsolutePath $BrowserPath
    $matched = @()
    foreach ($process in $Processes) {
        if (-not $process.CommandLine -or -not $process.ExecutablePath) { continue }
        if (-not [string]::Equals($process.ExecutablePath, $expectedBrowser, [StringComparison]::OrdinalIgnoreCase)) { continue }
        $match = [regex]::Match($process.CommandLine, '(?i)(?:^|\s)(?:"--user-data-dir=(?<whole>[^"]+)"|--user-data-dir="(?<quoted>[^"]+)"|--user-data-dir=(?<plain>[^\s"]+))(?=\s|$)')
        if (-not $match.Success) { continue }
        $candidate = if ($match.Groups['whole'].Success) { $match.Groups['whole'].Value } elseif ($match.Groups['quoted'].Success) { $match.Groups['quoted'].Value } else { $match.Groups['plain'].Value }
        try { $candidate = ConvertTo-MoodwallAbsolutePath $candidate } catch { continue }
        if ([string]::Equals($candidate, $profilePath, [StringComparison]::OrdinalIgnoreCase)) { $matched += $process }
    }
    return $matched
}

function Select-MoodwallTargetScreen {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][object[]]$Screens, [double]$PanelAspect = $script:PanelAspect)
    # The wall is the screen shaped like the panel; a laptop's own 16:9/16:10 screen
    # loses, and a primary screen wins only when nothing better is attached.
    $best = $null; $bestScore = [double]::MaxValue
    foreach ($screen in $Screens) {
        if ($screen.Width -le 0 -or $screen.Height -le 0) { continue }
        $score = [Math]::Abs(($screen.Width / $screen.Height) - $PanelAspect)
        if ($screen.Primary) { $score += 0.5 }
        if ($score -lt $bestScore) { $best = $screen; $bestScore = $score }
    }
    if (-not $best) { throw '연결된 화면을 찾지 못했습니다.' }
    return $best
}

function Get-MoodwallScreens {
    Add-Type -AssemblyName System.Windows.Forms
    foreach ($screen in [System.Windows.Forms.Screen]::AllScreens) {
        [pscustomobject]@{ X = $screen.Bounds.X; Y = $screen.Bounds.Y; Width = $screen.Bounds.Width; Height = $screen.Bounds.Height; Primary = $screen.Primary }
    }
}

function Get-MoodwallWindowArguments {
    param([Parameter(Mandatory = $true)][object]$Screen)
    # --start-fullscreen uses the monitor the window opens on; without a position
    # Chromium picks the primary (laptop) screen in extended mode.
    return @(('--window-position=' + [int]$Screen.X + ',' + [int]$Screen.Y), ('--window-size=' + [int]$Screen.Width + ',' + [int]$Screen.Height))
}

function Add-MoodwallWindowArguments {
    param([Parameter(Mandatory = $true)][string[]]$BrowserArguments, [AllowEmptyCollection()][string[]]$WindowArguments = @())
    # The quoted URL stays last.
    if (-not $WindowArguments.Count) { return $BrowserArguments }
    return @($BrowserArguments[0..($BrowserArguments.Count - 2)]) + $WindowArguments + @($BrowserArguments[-1])
}

function Get-MoodwallEnvOrigin {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$EnvPath)
    foreach ($line in [IO.File]::ReadAllLines($EnvPath)) {
        $match = [regex]::Match($line, '^\s*MOODWALL_PUBLIC_ORIGIN\s*=\s*(?<value>.*?)\s*$')
        if ($match.Success) { return $match.Groups['value'].Value.Trim([char[]]@([char]34, [char]39)).TrimEnd('/') }
    }
    throw '.env에 MOODWALL_PUBLIC_ORIGIN이 없습니다.'
}

function Get-MoodwallServerConfiguration {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ServerRoot,
        [Parameter(Mandatory = $true)][string[]]$ComposeFiles
    )
    $root = ConvertTo-MoodwallAbsolutePath $ServerRoot
    foreach ($file in $ComposeFiles) {
        if ($file -cnotmatch '^compose(\.[a-z0-9-]+)?\.yaml$') { throw ('허용하지 않는 Compose 파일 이름입니다: ' + $file) }
        if (-not [IO.File]::Exists([IO.Path]::Combine($root, $file))) { throw ('Compose 파일이 없습니다: ' + $file) }
    }
    return [ordered]@{ schemaVersion = 1; serverRoot = $root; composeFiles = @($ComposeFiles) }
}

function Read-MoodwallServerConfiguration {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$InstallRoot)
    $path = [IO.Path]::Combine((ConvertTo-MoodwallAbsolutePath $InstallRoot), 'server.json')
    if (-not [IO.File]::Exists($path)) { return $null }
    $raw = [IO.File]::ReadAllText($path)
    if ($raw.Length -gt 4096) { throw '서버 설정 파일을 확인해 주세요.' }
    $config = $raw | ConvertFrom-Json -ErrorAction Stop
    if ($config.schemaVersion -ne 1 -or $config.serverRoot -isnot [string]) { throw '서버 설정 파일 형식이 올바르지 않습니다.' }
    return [pscustomobject](Get-MoodwallServerConfiguration -ServerRoot $config.serverRoot -ComposeFiles @($config.composeFiles))
}

function Get-MoodwallComposeArguments {
    param([Parameter(Mandatory = $true)][object]$Server, [Parameter(Mandatory = $true)][string[]]$Command)
    $arguments = @('compose', '--project-directory', $Server.serverRoot)
    foreach ($file in $Server.composeFiles) { $arguments += @('-f', [IO.Path]::Combine($Server.serverRoot, $file)) }
    return $arguments + $Command
}

function Resolve-MoodwallDocker {
    $command = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $fallback = [IO.Path]::Combine($env:ProgramFiles, 'Docker', 'Docker', 'resources', 'bin', 'docker.exe')
    if ([IO.File]::Exists($fallback)) { return $fallback }
    throw 'Docker Desktop이 설치되어 있지 않습니다.'
}

function Test-MoodwallEndpoint {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$DisplayUrl, [int]$TimeoutMilliseconds = 5000)
    $url = ConvertTo-MoodwallDisplayUrl $DisplayUrl
    $request = [Net.HttpWebRequest]::Create($url)
    $request.Method = 'GET'
    $request.AllowAutoRedirect = $false
    if ($TimeoutMilliseconds -lt 1 -or $TimeoutMilliseconds -gt 10000) { throw '잘못된 연결 확인 제한 시간입니다.' }
    $request.Timeout = $TimeoutMilliseconds
    $request.ReadWriteTimeout = $TimeoutMilliseconds
    $request.UserAgent = 'Moodwall-Display-Launcher/1'
    $response = $null
    try {
        # Async wait bounds DNS/proxy discovery as well as TCP/HTTP response time.
        $pending = $request.GetResponseAsync()
        if (-not $pending.Wait($TimeoutMilliseconds)) { $request.Abort(); return $false }
        $response = $pending.Result
        return ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 300 -and $response.ContentType -match '^text/html(?:;|$)')
    } catch { return $false } finally { if ($response) { $response.Close() } }
}

function Wait-MoodwallEndpoint {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$DisplayUrl,
        [scriptblock]$Probe = { param($url) Test-MoodwallEndpoint -DisplayUrl $url },
        [scriptblock]$Delay = { Start-Sleep -Seconds 2 }
    )
    $url = ConvertTo-MoodwallDisplayUrl $DisplayUrl
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        if (& $Probe $url) { return $true }
        if ($attempt -lt 2) { & $Delay }
    }
    return $false
}

function Get-MoodwallLauncherMutexName {
    param([Parameter(Mandatory = $true)][string]$ProfileDirectory)
    $profile = ConvertTo-MoodwallAbsolutePath $ProfileDirectory
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $suffix = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($profile.ToLowerInvariant()))).Replace('-', '') } finally { $hash.Dispose() }
    return 'Local\MoodwallDisplayLauncher-' + $suffix
}

function Get-MoodwallLaunchDecision {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][object]$Plan,
        [ValidateSet('Display', 'Setup', 'Reopen')][string]$Role = 'Display',
        [AllowEmptyCollection()][object[]]$Processes = @(),
        [bool]$StaffSessionExists = $false
    )
    $existing = Get-MoodwallExistingDisplay -ProfileDirectory $Plan.ProfileDirectory -BrowserPath $Plan.BrowserPath -Processes $Processes
    # Staff setup shares the authentication profile, but never shares an open window.
    # Chromium's process command line cannot reliably identify the active tab URL.
    if ($Role -ne 'Display' -and $existing.Action -ne 'Launch') { return [pscustomobject]@{ Action = 'CloseRequired'; Process = $existing.Process } }
    if ($Role -eq 'Display' -and $StaffSessionExists -and $existing.Action -ne 'Launch') { return [pscustomobject]@{ Action = 'StaffWindowOpen'; Process = $existing.Process } }
    return $existing
}

function Get-MoodwallBrowserProcesses {
    param([Parameter(Mandatory = $true)][string]$BrowserPath)
    $processName = [IO.Path]::GetFileName((ConvertTo-MoodwallAbsolutePath $BrowserPath))
    # Candidate collection errors must not be treated as proof that no window exists.
    Get-CimInstance -ClassName Win32_Process -Filter ("Name = '" + $processName + "'") -ErrorAction Stop | ForEach-Object {
        try {
            $running = [Diagnostics.Process]::GetProcessById($_.ProcessId)
            [pscustomobject]@{ Id = $_.ProcessId; ExecutablePath = $_.ExecutablePath; CommandLine = $_.CommandLine; MainWindowHandle = $running.MainWindowHandle }
        } catch [ArgumentException] { } # Process exited during the snapshot.
    }
}

function Write-MoodwallLauncherEvent {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][ValidateSet('STARTED', 'ACTIVATED', 'BUSY', 'PROFILE_RUNNING', 'STAFF_WINDOW_OPEN', 'ACTIVATE_FAILED', 'ENDPOINT_UNAVAILABLE', 'LAUNCH_FAILED', 'SETUP_STARTED', 'SETUP_CLOSE_REQUIRED', 'SETUP_FAILED', 'POWER_ON', 'DOCKER_STARTING', 'DOCKER_UNAVAILABLE', 'SERVER_FAILED', 'SERVER_UNAVAILABLE', 'POWER_ON_FAILED', 'POWER_OFF', 'POWER_OFF_FAILED')][string]$Code
    )
    try {
        $logRoot = [IO.Path]::Combine((ConvertTo-MoodwallAbsolutePath $InstallRoot), 'Logs')
        Assert-MoodwallLocalPath $logRoot
        [void][IO.Directory]::CreateDirectory($logRoot)
        $path = [IO.Path]::Combine($logRoot, 'launcher.log')
        $previous = [IO.Path]::Combine($logRoot, 'launcher.previous.log')
        Assert-MoodwallLocalPath $path
        Assert-MoodwallLocalPath $previous
        if ([IO.File]::Exists($path) -and (Get-Item -LiteralPath $path).Length -ge 16384) {
            if ((Get-Item -LiteralPath $path).Length -le 16500) { [IO.File]::Copy($path, $previous, $true) }
            else { [IO.File]::WriteAllText($previous, '') } # Do not retain an oversized/tampered log.
            [IO.File]::WriteAllText($path, '')
        }
        # Fixed codes only: never persist URLs, exception text, credentials or content.
        [IO.File]::AppendAllText($path, ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ') + ' ' + $Code + [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))
    } catch { } # Diagnostics must never show UI or prevent the original recovery path.
}

function Get-MoodwallLauncherEvents {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)
    $path = [IO.Path]::Combine((ConvertTo-MoodwallAbsolutePath $InstallRoot), 'Logs', 'launcher.log')
    Assert-MoodwallLocalPath $path
    if (-not [IO.File]::Exists($path)) { return @() }
    if ((Get-Item -LiteralPath $path).Length -gt 32768) { return @('진단 기록의 크기가 올바르지 않습니다.') }
    return @(Get-Content -LiteralPath $path -Encoding UTF8 -Tail 12 | Where-Object { $_ -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z [A-Z_]+$' })
}

Export-ModuleMember -Function Select-MoodwallProfileProcesses, Select-MoodwallTargetScreen, Get-MoodwallScreens, Get-MoodwallWindowArguments, Add-MoodwallWindowArguments, Get-MoodwallEnvOrigin, Get-MoodwallServerConfiguration, Read-MoodwallServerConfiguration, Get-MoodwallComposeArguments, Resolve-MoodwallDocker, ConvertTo-MoodwallDisplayUrl, ConvertTo-MoodwallAbsolutePath, Assert-MoodwallLocalPath, Assert-MoodwallDeploymentUrl, Get-MoodwallBrowserCandidates, Resolve-MoodwallBrowser, Get-MoodwallLaunchPlan, Assert-MoodwallInstallRoot, Read-MoodwallConfiguration, Get-MoodwallExistingDisplay, Test-MoodwallEndpoint, Wait-MoodwallEndpoint, Get-MoodwallLauncherMutexName, Get-MoodwallLaunchDecision, Get-MoodwallBrowserProcesses, Write-MoodwallLauncherEvent, Get-MoodwallLauncherEvents
