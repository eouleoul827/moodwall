[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1')) -Force -DisableNameChecking

# No browser, desktop shortcut, COM, network or persistent installation is used.
$script:Passed = 0
function Assert-Equal {
    param($Actual, $Expected, [string]$Name)
    if ($Actual -cne $Expected) { throw "FAIL: $Name; expected [$Expected], got [$Actual]" }
    $script:Passed++
}
function Assert-Throws {
    param([scriptblock]$Action, [string]$Name)
    $threw = $false
    try { & $Action | Out-Null } catch { $threw = $true }
    if (-not $threw) { throw "FAIL: $Name; expected rejection" }
    $script:Passed++
}

function Invoke-ExpectedInstallerFailure {
    param([scriptblock]$Action)
    # Windows PowerShell 5.1 turns redirected native stderr into a terminating error under Stop.
    $ErrorActionPreference = 'Continue'
    & $Action 2>&1 | Out-Null
}
$testRoot = [IO.Path]::Combine([IO.Path]::GetTempPath(), ('moodwall-launcher-test-' + [Guid]::NewGuid().ToString('N')))
[void][IO.Directory]::CreateDirectory($testRoot)
try {
    Assert-Equal (ConvertTo-MoodwallDisplayUrl 'https://school.test') 'https://school.test/display' 'origin becomes display URL'
    Assert-Equal (ConvertTo-MoodwallDisplayUrl 'https://school.test/display') 'https://school.test/display' 'display path retained'
    Assert-Equal (ConvertTo-MoodwallDisplayUrl 'https://school.test:8443/') 'https://school.test:8443/display' 'explicit HTTPS port'
    foreach ($localUrl in @('http://localhost:5173', 'http://127.0.0.1:8000/display', 'http://[::1]:8000/')) {
        Assert-Equal ([Uri](ConvertTo-MoodwallDisplayUrl $localUrl)).IsLoopback $true 'loopback development allowed'
    }
    foreach ($badUrl in @('http://school.test', '//school.test', 'file:///C:/test', 'javascript:alert(1)', 'https://user:pass@school.test', 'https://school.test?token=abc', 'https://school.test?', 'https://school.test#secret', 'https://school.test#', 'https://school.test/operator', 'https://school.test/display/next', 'https://school.test/display/', 'https://school.test\display', ' https://school.test', 'https://school.test/" --incognito', "https://school.test`r`n--incognito")) {
        Assert-Throws { ConvertTo-MoodwallDisplayUrl $badUrl } ('unsafe URL: ' + $badUrl)
    }
    foreach ($example in @('https://example.com', 'https://foo.example.org', 'https://board.example', 'https://board.invalid', 'https://school.test')) {
        Assert-Throws { Assert-MoodwallDeploymentUrl $example } 'reserved host cannot install'
    }
    Assert-MoodwallDeploymentUrl 'http://localhost:5173'
    $script:Passed++
    foreach ($badPath in @('relative\folder', 'C:relative', '\\server\share\profile', 'C:\bad"path')) {
        Assert-Throws { ConvertTo-MoodwallAbsolutePath $badPath } 'reject ambiguous/unsafe path'
    }

    $spaceRoot = [IO.Path]::Combine($testRoot, 'Install With Spaces')
    $desktopRoot = [IO.Path]::Combine($testRoot, 'Desktop With Spaces')
    $edgePath = [IO.Path]::Combine($testRoot, 'Edge Browser', 'msedge.exe')
    $chromePath = [IO.Path]::Combine($testRoot, 'Chrome Browser', 'chrome.exe')
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($edgePath))
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($chromePath))
    [IO.File]::WriteAllText($edgePath, '')
    [IO.File]::WriteAllText($chromePath, '')
    $edge = [pscustomobject]@{ Kind = 'Edge'; Path = $edgePath }
    $chrome = [pscustomobject]@{ Kind = 'Chrome'; Path = $chromePath }
    Assert-Equal (Resolve-MoodwallBrowser -Candidates @($edge, $chrome)).Kind 'Edge' 'Edge selected first'
    Assert-Equal (Resolve-MoodwallBrowser -Candidates @([pscustomobject]@{Kind='Edge';Path=($edgePath + '.missing')}, $chrome)).Kind 'Chrome' 'Chrome fallback'
    $plan = Get-MoodwallLaunchPlan -DisplayUrl 'https://school.test' -InstallRoot $spaceRoot -DesktopDirectory $desktopRoot -Browser $edge
    $chromePlan = Get-MoodwallLaunchPlan -DisplayUrl 'https://school.test' -InstallRoot $spaceRoot -DesktopDirectory $desktopRoot -Browser $chrome
    Assert-Equal ($plan.ProfileDirectory -ne $chromePlan.ProfileDirectory) $true 'browser profiles separated'
    Assert-Equal ($plan.ShortcutArguments.Contains('-File "' + [IO.Path]::Combine($spaceRoot, 'Start-Display.ps1') + '"')) $true 'shortcut script argument quotes spaces'
    Assert-Equal ($plan.BrowserArguments.Contains('--user-data-dir="' + $plan.ProfileDirectory + '"')) $true 'browser profile argument quotes spaces'
    Assert-Equal (($plan.BrowserArguments -join ' ') -match '(?i)kiosk|incognito|inprivate|disable-web-security|remote-debugging') $false 'persistent secure normal browser flags'
    Assert-Equal ($plan.ShortcutArguments.Contains('-STA -WindowStyle Hidden')) $true 'dialog helper STA and hidden'
    Assert-Equal $plan.ShortcutTarget ([IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')) 'full PowerShell executable path'
    Assert-Equal $plan.ShortcutPath ([IO.Path]::Combine($desktopRoot, '무드월 전광판 시작.lnk')) 'employee Korean shortcut'
    Assert-Equal (($plan.Configuration.Keys | Sort-Object) -join ',') 'displayUrl,schemaVersion' 'configuration contains URL/schema only'
    Assert-Equal ([IO.Directory]::Exists($spaceRoot)) $false 'planning performs no installation'
    Assert-MoodwallInstallRoot $spaceRoot
    $script:Passed++
    [void][IO.Directory]::CreateDirectory($spaceRoot)
    [IO.File]::WriteAllText([IO.Path]::Combine($spaceRoot, 'unrelated.txt'), 'preserve me')
    Assert-Throws { Assert-MoodwallInstallRoot $spaceRoot } 'unowned nonempty folder protected'
    Assert-Equal ([IO.File]::ReadAllText([IO.Path]::Combine($spaceRoot, 'unrelated.txt'))) 'preserve me' 'unrelated file unchanged'
    Remove-Item -LiteralPath ([IO.Path]::Combine($spaceRoot, 'unrelated.txt'))
    [IO.File]::WriteAllText([IO.Path]::Combine($spaceRoot, 'moodwall-display-launcher.marker'), 'MoodwallDisplayLauncher:1')
    [IO.File]::WriteAllText([IO.Path]::Combine($spaceRoot, 'config.json'), ($plan.Configuration | ConvertTo-Json))
    Assert-MoodwallInstallRoot $spaceRoot
    $script:Passed++
    Assert-Equal (Read-MoodwallConfiguration $spaceRoot).displayUrl 'https://school.test/display' 'stored configuration round trip'
    [IO.File]::WriteAllText([IO.Path]::Combine($spaceRoot, 'config.json'), '{"schemaVersion":1,"displayUrl":"https://school.test","deviceToken":"do-not-store"}')
    Assert-Throws { Read-MoodwallConfiguration $spaceRoot } 'extra secret field rejected'
    [IO.File]::WriteAllText([IO.Path]::Combine($spaceRoot, 'config.json'), '{"schemaVersion":2,"displayUrl":"https://school.test"}')
    Assert-Throws { Read-MoodwallConfiguration $spaceRoot } 'unknown schema rejected'

    $displayProcess = [pscustomobject]@{ Id=123; ExecutablePath=$edgePath; CommandLine=('"' + $edgePath + '" --user-data-dir="' + $plan.ProfileDirectory + '" https://school.test/display'); MainWindowHandle=42 }
    $personalProcess = [pscustomobject]@{ Id=124; ExecutablePath=$edgePath; CommandLine=('"' + $edgePath + '" --user-data-dir="' + $plan.ProfileDirectory + '-personal"'); MainWindowHandle=43 }
    Assert-Equal (Get-MoodwallExistingDisplay -ProfileDirectory $plan.ProfileDirectory -BrowserPath $edgePath -Processes @($personalProcess, $displayProcess)).Action 'Activate' 'existing display activated'
    Assert-Equal (Get-MoodwallExistingDisplay -ProfileDirectory $plan.ProfileDirectory -BrowserPath $edgePath -Processes @($personalProcess)).Action 'Launch' 'personal browser is ignored'
    $displayProcess.MainWindowHandle = 0
    Assert-Equal (Get-MoodwallExistingDisplay -ProfileDirectory $plan.ProfileDirectory -BrowserPath $edgePath -Processes @($displayProcess)).Action 'AlreadyRunning' 'headless profile gets staff guidance'
    $displayProcess.ExecutablePath = $chromePath
    Assert-Equal (Get-MoodwallExistingDisplay -ProfileDirectory $plan.ProfileDirectory -BrowserPath $edgePath -Processes @($displayProcess)).Action 'Launch' 'other executable cannot match'
    $displayProcess.ExecutablePath = $edgePath
    $displayProcess.MainWindowHandle = 42
    $displayProcess.CommandLine = '"' + $edgePath + '" "--user-data-dir=' + $plan.ProfileDirectory + '"'
    Assert-Equal (Get-MoodwallExistingDisplay -ProfileDirectory $plan.ProfileDirectory -BrowserPath $edgePath -Processes @($displayProcess)).Action 'Activate' 'whole quoted profile argument'
    $script:Probes = 0; $script:Delays = 0
    $result = Wait-MoodwallEndpoint -DisplayUrl 'https://school.test' -Probe { param($url) $script:Probes++; return $false } -Delay { $script:Delays++ }
    Assert-Equal $result $false 'unavailable endpoint remains failure'
    Assert-Equal $script:Probes 3 'preflight retries bounded'
    Assert-Equal $script:Delays 2 'bounded retry delays'
    $script:Probes = 0
    Assert-Equal (Wait-MoodwallEndpoint -DisplayUrl 'https://school.test' -Probe { param($url) $script:Probes++; return ($script:Probes -eq 2) } -Delay {}) $true 'preflight recovers without network'
    Assert-Equal $script:Probes 2 'success stops retry loop'

    # The actual developer entry point is exercised in dry-run mode as a child PS5.1 process.
    $dryRoot = [IO.Path]::Combine($testRoot, 'Dry Run No Installation')
    $installerPath = [IO.Path]::Combine($PSScriptRoot, 'Install.ps1')
    $powershell = [IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    $output = & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installerPath -DisplayUrl 'https://school.test' -InstallRoot $dryRoot -DesktopDirectory $desktopRoot -PlanOnly
    Assert-Equal $LASTEXITCODE 0 'installer PlanOnly succeeds'
    $dryPlan = ($output -join "`n") | ConvertFrom-Json
    Assert-Equal $dryPlan.DisplayUrl 'https://school.test/display' 'installer plan URL validated'
    Assert-Equal ([IO.Directory]::Exists($dryRoot)) $false 'installer PlanOnly does not write install directory'
    Assert-Equal ([IO.Directory]::Exists($desktopRoot)) $false 'installer PlanOnly does not create desktop'
    $installedSimulation = [IO.Path]::Combine($testRoot, 'Installed Source With Spaces')
    [void][IO.Directory]::CreateDirectory($installedSimulation)
    foreach ($file in @('DisplayLauncher.psm1', 'Start-Display.ps1')) {
        Copy-Item -LiteralPath ([IO.Path]::Combine($PSScriptRoot, $file)) -Destination ([IO.Path]::Combine($installedSimulation, $file))
    }
    [IO.File]::WriteAllText([IO.Path]::Combine($installedSimulation, 'config.json'), '{"schemaVersion":1,"displayUrl":"https://school.test/display"}')
    $output = & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ([IO.Path]::Combine($installedSimulation, 'Start-Display.ps1')) -PlanOnly
    Assert-Equal $LASTEXITCODE 0 'installed launcher dry-run accepts stored configuration'
    $installedPlan = ($output -join "`n") | ConvertFrom-Json
    Assert-Equal $installedPlan.DisplayUrl 'https://school.test/display' 'installed launcher uses validated stored URL'
    Assert-Equal ([IO.Directory]::Exists([IO.Path]::Combine($installedSimulation, 'Profiles'))) $false 'launcher dry-run does not create a browser profile'
    Assert-Equal $installedPlan.InstallRoot $installedSimulation 'launcher paths anchored to its own source directory'

    # Public and staff destinations share credentials, while role entry stays fixed and separate.
    Assert-Equal $plan.SetupUrl 'https://school.test/display/setup' 'staff URL derived from the validated origin only'
    Assert-Equal $plan.StaffShortcutPath ([IO.Path]::Combine($desktopRoot, '무드월 직원 설정·점검.lnk')) 'explicit staff-only shortcut'
    Assert-Equal ($plan.StaffShortcutArguments.Contains('-File "' + [IO.Path]::Combine($spaceRoot, 'Start-Staff.ps1') + '"')) $true 'staff helper path quotes spaces'
    Assert-Equal ($plan.SetupBrowserArguments.Contains('--user-data-dir="' + $plan.ProfileDirectory + '"')) $true 'setup retains the original pairing profile'
    Assert-Equal ($plan.SetupBrowserArguments.Contains('--start-fullscreen')) $false 'staff setup does not force itself fullscreen'
    Assert-Equal ($plan.BrowserArguments.Contains('"https://school.test/display"')) $true 'public URL unchanged'
    Assert-Equal (Get-MoodwallLauncherMutexName $plan.ProfileDirectory) (Get-MoodwallLauncherMutexName $plan.ProfileDirectory.ToUpperInvariant()) 'role mutex uses case-insensitive profile path'
    Assert-Equal ((Get-MoodwallLauncherMutexName $plan.ProfileDirectory) -ne (Get-MoodwallLauncherMutexName $chromePlan.ProfileDirectory)) $true 'different profiles have separate launch fences'
    $displayProcess.CommandLine = '"' + $edgePath + '" --user-data-dir="' + $plan.ProfileDirectory + '" https://school.test/display'
    Assert-Equal (Get-MoodwallLaunchDecision -Plan $plan -Processes @($displayProcess)).Action 'Activate' 'normal repeat launch preserves existing public playback'
    Assert-Equal (Get-MoodwallLaunchDecision -Plan $plan -Processes @($displayProcess) -StaffSessionExists $true).Action 'StaffWindowOpen' 'public click cannot activate a known staff window'
    Assert-Equal (Get-MoodwallLaunchDecision -Plan $plan -Role Setup -Processes @($displayProcess)).Action 'CloseRequired' 'staff setup requires closing public window first'
    Assert-Equal (Get-MoodwallLaunchDecision -Plan $plan -Role Reopen -Processes @($displayProcess)).Action 'CloseRequired' 'explicit fresh start cannot reload playing window'
    Assert-Equal (Get-MoodwallLaunchDecision -Plan $plan -Role Setup -Processes @($personalProcess)).Action 'Launch' 'staff setup ignores personal profile windows'
    Assert-Equal (Get-MoodwallLaunchDecision -Plan $plan -StaffSessionExists $true -Processes @()).Action 'Launch' 'closed staff window permits a fresh public launch'
    $displayProcess.MainWindowHandle = 0
    Assert-Equal (Get-MoodwallLaunchDecision -Plan $plan -Processes @($displayProcess) -StaffSessionExists $true).Action 'StaffWindowOpen' 'background staff profile also prevents accidental public reuse'
    $displayProcess.MainWindowHandle = 42
    [IO.File]::WriteAllText([IO.Path]::Combine($spaceRoot, 'Start-Staff.ps1'), '')
    [void][IO.Directory]::CreateDirectory([IO.Path]::Combine($spaceRoot, 'Logs'))
    [IO.File]::WriteAllText($plan.StaffSessionPath, 'MoodwallStaffWindow:1')
    Assert-MoodwallInstallRoot $spaceRoot
    $script:Passed++

    $logRoot = [IO.Path]::Combine($testRoot, 'Safe Log Fixture')
    [void][IO.Directory]::CreateDirectory([IO.Path]::Combine($logRoot, 'Logs'))
    [IO.File]::WriteAllText([IO.Path]::Combine($logRoot, 'Logs', 'launcher.log'), ('2026-11-05T00:00:00Z ENDPOINT_UNAVAILABLE' + [Environment]::NewLine) * 410)
    for ($entry = 0; $entry -lt 12; $entry++) { Write-MoodwallLauncherEvent -InstallRoot $logRoot -Code ENDPOINT_UNAVAILABLE }
    $logFiles = @(Get-ChildItem -LiteralPath ([IO.Path]::Combine($logRoot, 'Logs')) -File)
    Assert-Equal ($logFiles.Count -le 2) $true 'launcher diagnostics retain at most two files'
    Assert-Equal (@($logFiles | Where-Object { $_.Length -gt 16500 }).Count) 0 'launcher diagnostic file sizes bounded'
    Assert-Equal (@(Get-MoodwallLauncherEvents $logRoot).Count) 12 'staff sees only a short recent diagnostic tail'
    Assert-Throws { Write-MoodwallLauncherEvent -InstallRoot $logRoot -Code 'SECRET_COOKIE_VALUE' } 'raw exception or secret cannot become a diagnostic code'
    Assert-Equal (([IO.File]::ReadAllText([IO.Path]::Combine($logRoot, 'Logs', 'launcher.log'))) -match 'https?://|COOKIE|password') $false 'diagnostics contain no URL or credential fields'
    $brokenLogRoot = [IO.Path]::Combine($testRoot, 'Broken Log Fixture')
    [void][IO.Directory]::CreateDirectory($brokenLogRoot)
    [IO.File]::WriteAllText([IO.Path]::Combine($brokenLogRoot, 'Logs'), 'not a directory')
    Write-MoodwallLauncherEvent -InstallRoot $brokenLogRoot -Code LAUNCH_FAILED
    $script:Passed++ # Logging failure is silent and non-fatal.

    foreach ($sourceFile in @('DisplayLauncher.psm1', 'Install.ps1', 'Start-Display.ps1', 'Start-Staff.ps1', 'Test-Launcher.ps1')) {
        $parseTokens = $null; $parseErrors = $null
        [void][Management.Automation.Language.Parser]::ParseFile([IO.Path]::Combine($PSScriptRoot, $sourceFile), [ref]$parseTokens, [ref]$parseErrors)
        Assert-Equal $parseErrors.Count 0 ('PowerShell 5.1 syntax: ' + $sourceFile)
    }
    $publicSource = [IO.File]::ReadAllText([IO.Path]::Combine($PSScriptRoot, 'Start-Display.ps1'))
    Assert-Equal ($publicSource -match 'MessageBox|Show-StaffMessage|Read-Host|Out-GridView|Forms\.') $false 'public launcher has no modal UI path'
    $staffSource = [IO.File]::ReadAllText([IO.Path]::Combine($PSScriptRoot, 'Start-Staff.ps1'))
    Assert-Equal ($staffSource -match 'Stop-Process|taskkill|SendKeys|Stop-Service') $false 'staff recovery does not kill browsers or inject blind keystrokes'
    Assert-Equal ($staffSource -match 'Method\s*=\s*[''\"]POST|/api/display/pair|SetExecutionPolicy') $false 'staff helper cannot bypass pairing or global policy'
    Copy-Item -LiteralPath ([IO.Path]::Combine($PSScriptRoot, 'Start-Staff.ps1')) -Destination ([IO.Path]::Combine($installedSimulation, 'Start-Staff.ps1'))
    $output = & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ([IO.Path]::Combine($installedSimulation, 'Start-Staff.ps1')) -PlanOnly
    Assert-Equal $LASTEXITCODE 0 'staff dry run does not require UI'
    $staffPlan = ($output -join "`n") | ConvertFrom-Json
    Assert-Equal $staffPlan.ProfileDirectory $installedPlan.ProfileDirectory 'staff and public dry-run use identical persistent profile'
    Assert-Equal ([IO.File]::Exists($staffPlan.StaffSessionPath)) $false 'staff dry run creates no role marker'

    # Exercise real public entry point with a test-only module shim. Any browser/COM/network
    # call here throws, so success proves failure exits happen before external side effects.
    $silentFixture = [IO.Path]::Combine($testRoot, 'Silent Public Fixture')
    [void][IO.Directory]::CreateDirectory($silentFixture)
    Copy-Item -LiteralPath ([IO.Path]::Combine($PSScriptRoot, 'Start-Display.ps1')) -Destination ([IO.Path]::Combine($silentFixture, 'Start-Display.ps1'))
    [IO.File]::WriteAllText([IO.Path]::Combine($silentFixture, 'config.json'), '{"schemaVersion":1,"displayUrl":"https://school.edu/display"}')
    $moduleSource = [IO.File]::ReadAllText([IO.Path]::Combine($PSScriptRoot, 'DisplayLauncher.psm1'))
    $shim = @'
function Resolve-MoodwallBrowser { [pscustomobject]@{ Kind = 'Edge'; Path = 'C:\Test Browser\msedge.exe' } }
function Get-MoodwallBrowserProcesses { @() }
function Wait-MoodwallEndpoint { $false }
function Start-Process { throw 'Unexpected browser invocation in silent failure test' }
Export-ModuleMember -Function *
'@
    $bomEncoding = New-Object Text.UTF8Encoding($true)
    [IO.File]::WriteAllText([IO.Path]::Combine($silentFixture, 'DisplayLauncher.psm1'), ($moduleSource + "`r`n" + $shim), $bomEncoding)
    $output = & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ([IO.Path]::Combine($silentFixture, 'Start-Display.ps1'))
    Assert-Equal $LASTEXITCODE 1 'public offline path exits instead of showing retry dialog'
    Assert-Equal ($output -join '') '' 'public offline failure produces no visible console output'
    Assert-Equal (@(Get-MoodwallLauncherEvents $silentFixture)[0] -match ' ENDPOINT_UNAVAILABLE$') $true 'offline failure has only fixed-code local diagnostics'
    Assert-Equal ([IO.Directory]::Exists([IO.Path]::Combine($silentFixture, 'Profiles'))) $false 'offline failure does not launch or create browser profile'
    [IO.File]::WriteAllText([IO.Path]::Combine($silentFixture, 'config.json'), '{"schemaVersion":1,"displayUrl":"https://school.edu/display?secret=bad"}')
    $output = & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ([IO.Path]::Combine($silentFixture, 'Start-Display.ps1'))
    Assert-Equal $LASTEXITCODE 1 'invalid public configuration also fails silently'
    Assert-Equal ($output -join '') '' 'invalid public configuration has no visible output'
    Assert-Equal (@(Get-MoodwallLauncherEvents $silentFixture)[-1] -match ' LAUNCH_FAILED$') $true 'configuration failure has a safe local code'
    Assert-Equal (([IO.File]::ReadAllText([IO.Path]::Combine($silentFixture, 'Logs', 'launcher.log'))) -match 'secret|bad|school.edu') $false 'config failure cannot leak raw URL into log'
    [IO.File]::WriteAllText([IO.Path]::Combine($silentFixture, 'config.json'), '{"schemaVersion":1,"displayUrl":"https://school.edu/display"}')
    [IO.File]::WriteAllText([IO.Path]::Combine($silentFixture, 'staff-session.marker'), 'MoodwallStaffWindow:1')
    $staffGuardShim = @'
function Resolve-MoodwallBrowser { [pscustomobject]@{ Kind = 'Edge'; Path = 'C:\Test Browser\msedge.exe' } }
function Get-MoodwallBrowserProcesses {
    [pscustomobject]@{ Id = 123; ExecutablePath = 'C:\Test Browser\msedge.exe'; CommandLine = ('"C:\Test Browser\msedge.exe" --user-data-dir="' + [IO.Path]::Combine($PSScriptRoot, 'Profiles', 'Edge') + '" https://school.edu/display/setup'); MainWindowHandle = 42 }
}
function Wait-MoodwallEndpoint { throw 'Network check must not run before the staff-role guard' }
function Start-Process { throw 'Browser must not be spawned while a staff window is active' }
Export-ModuleMember -Function *
'@
    [IO.File]::WriteAllText([IO.Path]::Combine($silentFixture, 'DisplayLauncher.psm1'), ($moduleSource + "`r`n" + $staffGuardShim), $bomEncoding)
    $output = & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ([IO.Path]::Combine($silentFixture, 'Start-Display.ps1'))
    Assert-Equal $LASTEXITCODE 0 'public click refuses staff window reuse before any network or browser action'
    Assert-Equal ($output -join '') '' 'staff window guard is silent on public launch'
    Assert-Equal (@(Get-MoodwallLauncherEvents $silentFixture)[-1] -match ' STAFF_WINDOW_OPEN$') $true 'staff role cannot fall through to activation'

    # --- Library LED wall (2046 x 768) and laptop-as-server mode ---
    $laptop = [pscustomobject]@{ X = 0; Y = 0; Width = 1920; Height = 1080; Primary = $true }
    $wall = [pscustomobject]@{ X = 1920; Y = 0; Width = 2046; Height = 768; Primary = $false }
    Assert-Equal (Select-MoodwallTargetScreen @($laptop, $wall)).X 1920 'extended desktop opens on the LED wall'
    Assert-Equal (Select-MoodwallTargetScreen @($wall, $laptop)).X 1920 'screen order does not matter'
    $wallOnly = [pscustomobject]@{ X = 0; Y = 0; Width = 2046; Height = 768; Primary = $true }
    Assert-Equal (Select-MoodwallTargetScreen @($wallOnly)).Width 2046 'second-screen-only mode uses the only screen'
    Assert-Equal (Select-MoodwallTargetScreen @($laptop)).Width 1920 'laptop alone still opens'
    Assert-Throws { Select-MoodwallTargetScreen @([pscustomobject]@{ X = 0; Y = 0; Width = 0; Height = 0; Primary = $true }) } 'no usable screen'
    $windowArgs = @(Get-MoodwallWindowArguments $wall)
    Assert-Equal ($windowArgs -join ' ') '--window-position=1920,0 --window-size=2046,768' 'window placed on the wall'
    $placed = @(Add-MoodwallWindowArguments -BrowserArguments $plan.BrowserArguments -WindowArguments $windowArgs)
    Assert-Equal $placed[-1] '"https://school.test/display"' 'URL remains the last browser argument'
    Assert-Equal ($placed -contains '--window-size=2046,768') $true 'window size argument added'
    Assert-Equal (@(Add-MoodwallWindowArguments -BrowserArguments $plan.BrowserArguments).Count) $plan.BrowserArguments.Count 'no screen keeps original arguments'
    Assert-Equal ($plan.BrowserArguments -contains '--hide-crash-restore-bubble') $true 'no restore bubble on the public wall'

    $repo = [IO.Path]::Combine($testRoot, 'Server Repo')
    [void][IO.Directory]::CreateDirectory([IO.Path]::Combine($repo, 'frontend', 'dist'))
    foreach ($file in @('compose.yaml', 'compose.frontend.yaml', 'compose.gemini.yaml', 'compose.library.yaml')) { [IO.File]::WriteAllText([IO.Path]::Combine($repo, $file), 'services: {}') }
    [IO.File]::WriteAllText([IO.Path]::Combine($repo, 'frontend', 'dist', 'index.html'), '<!doctype html>')
    $envPath = [IO.Path]::Combine($repo, '.env')
    [IO.File]::WriteAllText($envPath, "MOODWALL_BOARD_ID=x`r`nMOODWALL_PUBLIC_ORIGIN='https://moodwall.school.edu/'`r`n")
    Assert-Equal (Get-MoodwallEnvOrigin $envPath) 'https://moodwall.school.edu' 'origin read from .env without quotes or slash'
    $server = Get-MoodwallServerConfiguration -ServerRoot $repo -ComposeFiles @('compose.yaml', 'compose.library.yaml')
    Assert-Equal $server.serverRoot $repo 'server root normalized'
    Assert-Throws { Get-MoodwallServerConfiguration -ServerRoot $repo -ComposeFiles @('..\evil.yaml') } 'compose file names are restricted'
    Assert-Throws { Get-MoodwallServerConfiguration -ServerRoot $repo -ComposeFiles @('compose.missing.yaml') } 'missing compose file rejected'
    $composeArgs = @(Get-MoodwallComposeArguments ([pscustomobject]$server) @('up', '-d'))
    Assert-Equal ($composeArgs[0..2] -join '|') ('compose|--project-directory|' + $repo) 'compose project directory pinned'
    Assert-Equal $composeArgs[-1] '-d' 'compose command appended last'

    $serverInstall = [IO.Path]::Combine($testRoot, 'Server Install')
    $output = & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installerPath -ServerRoot $repo -InstallRoot $serverInstall -DesktopDirectory $desktopRoot -PlanOnly
    Assert-Equal $LASTEXITCODE 0 'server-mode PlanOnly succeeds without typing the URL'
    $serverPlan = ($output -join "`n") | ConvertFrom-Json
    Assert-Equal $serverPlan.DisplayUrl 'https://moodwall.school.edu/display' 'wall address comes from PUBLIC_ORIGIN'
    Assert-Equal ($serverPlan.Server.composeFiles -join ',') 'compose.yaml,compose.frontend.yaml,compose.gemini.yaml,compose.library.yaml' 'default library compose stack'
    Assert-Equal ([IO.Path]::GetFileName($serverPlan.PowerOnShortcutPath)) '무드월 켜기.lnk' 'power-on icon'
    Assert-Equal ([IO.Path]::GetFileName($serverPlan.PowerOffShortcutPath)) '무드월 끄기.lnk' 'power-off icon'
    Assert-Equal ([IO.Directory]::Exists($serverInstall)) $false 'server-mode PlanOnly writes nothing'
    Invoke-ExpectedInstallerFailure { & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installerPath -ServerRoot $repo -DisplayUrl 'https://other.school.edu' -InstallRoot $serverInstall -DesktopDirectory $desktopRoot -PlanOnly }
    Assert-Equal ($LASTEXITCODE -ne 0) $true 'launcher URL different from PUBLIC_ORIGIN is refused'
    [IO.File]::WriteAllText($envPath, "MOODWALL_PUBLIC_ORIGIN=http://localhost:8000`r`n")
    Invoke-ExpectedInstallerFailure { & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installerPath -ServerRoot $repo -InstallRoot $serverInstall -DesktopDirectory $desktopRoot -PlanOnly }
    Assert-Equal ($LASTEXITCODE -ne 0) $true 'localhost PUBLIC_ORIGIN cannot serve phones and is refused'
    [IO.File]::WriteAllText($envPath, "MOODWALL_PUBLIC_ORIGIN=https://moodwall.school.edu`r`n")
    Remove-Item -LiteralPath ([IO.Path]::Combine($repo, 'frontend', 'dist', 'index.html'))
    Invoke-ExpectedInstallerFailure { & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installerPath -ServerRoot $repo -InstallRoot $serverInstall -DesktopDirectory $desktopRoot -PlanOnly }
    Assert-Equal ($LASTEXITCODE -ne 0) $true 'missing frontend build is refused'
    Invoke-ExpectedInstallerFailure { & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installerPath -InstallRoot $serverInstall -DesktopDirectory $desktopRoot -PlanOnly }
    Assert-Equal ($LASTEXITCODE -ne 0) $true 'neither URL nor server root is refused'
    $profileProcesses = @(Select-MoodwallProfileProcesses -ProfileDirectory $plan.ProfileDirectory -BrowserPath $plan.BrowserPath -Processes @($displayProcess, $personalProcess))
    Assert-Equal $profileProcesses.Count 1 'power-off closes only the dedicated Moodwall profile'
    # The last checks deliberately run failing installers; CI's dot-sourced shell exits with $LASTEXITCODE.
    $global:LASTEXITCODE = 0
    Write-Output ("PASS: $script:Passed launcher checks; no UI, network or real installation performed.")
} finally {
    # Delete only the unique temporary test directory whose absolute ancestry is checked.
    $resolvedTest = [IO.Path]::GetFullPath($testRoot)
    $expectedParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedTest.StartsWith($expectedParent, [StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($resolvedTest) -notlike 'moodwall-launcher-test-*') {
        throw 'Unsafe test cleanup target; refusing deletion.'
    }
    Remove-Item -LiteralPath $resolvedTest -Recurse -Force
}
