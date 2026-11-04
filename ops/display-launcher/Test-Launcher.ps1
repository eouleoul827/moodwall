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
