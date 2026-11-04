Set-StrictMode -Version Latest

$script:MarkerName = 'moodwall-display-launcher.marker'
$script:MarkerValue = 'MoodwallDisplayLauncher:1'
$script:ShortcutName = '무드월 전광판 시작.lnk'

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
        BrowserArguments = @('--no-first-run', '--no-default-browser-check', '--new-window', '--start-fullscreen', ('--user-data-dir="' + $profilePath + '"'), ('"' + $url + '"'))
        ShortcutPath = [IO.Path]::Combine($desktopPath, $script:ShortcutName)
        ShortcutTarget = $powershellPath
        ShortcutArguments = '-NoLogo -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launcherPath + '"'
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
    $allowed = @($script:MarkerName, 'DisplayLauncher.psm1', 'Start-Display.ps1', 'config.json', 'Profiles')
    foreach ($item in $items) {
        if ($item.Name -notin $allowed -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw '설치 폴더에 알 수 없는 파일 또는 연결 항목이 있어 갱신을 중단했습니다.'
        }
        if ($item.PSIsContainer -and $item.Name -ne 'Profiles') { throw '설치 파일 위치에 폴더가 있습니다.' }
        if (-not $item.PSIsContainer -and $item.Name -eq 'Profiles') { throw '프로필 위치에 파일이 있습니다.' }
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
    $window = @($matched | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1)
    if ($window.Count) { return [pscustomobject]@{ Action = 'Activate'; Process = $window[0] } }
    if ($matched.Count) { return [pscustomobject]@{ Action = 'AlreadyRunning'; Process = $matched[0] } }
    return [pscustomobject]@{ Action = 'Launch'; Process = $null }
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

Export-ModuleMember -Function ConvertTo-MoodwallDisplayUrl, ConvertTo-MoodwallAbsolutePath, Assert-MoodwallLocalPath, Assert-MoodwallDeploymentUrl, Get-MoodwallBrowserCandidates, Resolve-MoodwallBrowser, Get-MoodwallLaunchPlan, Assert-MoodwallInstallRoot, Read-MoodwallConfiguration, Get-MoodwallExistingDisplay, Test-MoodwallEndpoint, Wait-MoodwallEndpoint
