param(
    [string[]]$Targets = @("10.253.1.43"),
    [int[]]$Ports = @(8765),
    [string]$HealthPath = "/api/health",
    [string]$ReportDir = "reports",
    [int]$TimeoutSec = 5
)

$ErrorActionPreference = "Stop"

function New-CheckResult {
    param(
        [string]$Target,
        [int]$Port,
        [string]$Probe,
        [bool]$Succeeded,
        [Nullable[int]]$StatusCode = $null,
        [string]$Detail = ""
    )

    [pscustomobject]@{
        Target = $Target
        Port = $Port
        Probe = $Probe
        Succeeded = $Succeeded
        StatusCode = $StatusCode
        Detail = $Detail
    }
}

function New-HealthUrl {
    param([string]$Target, [int]$Port)

    $path = if ($HealthPath.StartsWith("/")) { $HealthPath } else { "/$HealthPath" }
    "http://${Target}:${Port}${path}"
}

function Invoke-IwrHealth {
    param([string]$Target, [int]$Port, [string]$ProbeName)

    $url = New-HealthUrl -Target $Target -Port $Port
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $TimeoutSec
        $body = [string]$response.Content
        New-CheckResult -Target $Target -Port $Port -Probe $ProbeName -Succeeded ($response.StatusCode -eq 200 -and -not [string]::IsNullOrWhiteSpace($body)) -StatusCode $response.StatusCode -Detail "Body length: $($body.Length)"
    } catch {
        $statusCode = $null
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        New-CheckResult -Target $Target -Port $Port -Probe $ProbeName -Succeeded $false -StatusCode $statusCode -Detail $_.Exception.Message
    }
}

function Invoke-CurlHealth {
    param([string]$Target, [int]$Port)

    $url = New-HealthUrl -Target $Target -Port $Port
    try {
        $stdoutPath = [System.IO.Path]::GetTempFileName()
        $stderrPath = [System.IO.Path]::GetTempFileName()
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $curlCommand = 'curl.exe -sS -i -v --connect-timeout {0} "{1}" 1> "{2}" 2> "{3}"' -f $TimeoutSec, $url, $stdoutPath, $stderrPath
            & cmd.exe /d /c $curlCommand
            $exitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        $stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -Raw -LiteralPath $stdoutPath } else { "" }
        $stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -Raw -LiteralPath $stderrPath } else { "" }
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
        $text = @($stderr, $stdout) -join "`n"
        $statusCode = $null
        $matches = [regex]::Matches($text, "HTTP/\S+\s+(\d{3})")
        if ($matches.Count -gt 0) {
            $statusCode = [int]$matches[$matches.Count - 1].Groups[1].Value
        }
        New-CheckResult -Target $Target -Port $Port -Probe "curl.exe" -Succeeded ($exitCode -eq 0 -and $statusCode -eq 200) -StatusCode $statusCode -Detail ($text.Trim() -replace "`r", "")
    } catch {
        New-CheckResult -Target $Target -Port $Port -Probe "curl.exe" -Succeeded $false -Detail $_.Exception.Message
    }
}

function Get-InterestingOutboundBlocks {
    try {
        $rules = Get-NetFirewallRule -Direction Outbound -Action Block -Enabled True -ErrorAction Stop
    } catch {
        return @([pscustomobject]@{ Rule = "ERROR"; Detail = $_.Exception.Message })
    }

    $interesting = @()
    foreach ($rule in $rules) {
        $ports = @()
        $apps = @()
        try {
            $ports = $rule | Get-NetFirewallPortFilter | ForEach-Object {
                "Protocol=$($_.Protocol);LocalPort=$($_.LocalPort);RemotePort=$($_.RemotePort)"
            }
        } catch {
            $ports = @("PortFilterError=$($_.Exception.Message)")
        }
        try {
            $apps = $rule | Get-NetFirewallApplicationFilter | ForEach-Object {
                "Program=$($_.Program)"
            }
        } catch {
            $apps = @("ApplicationFilterError=$($_.Exception.Message)")
        }

        $blob = @(
            $rule.DisplayName,
            $rule.Name,
            $rule.Description,
            $ports,
            $apps
        ) -join " "

        if ($blob -match "(?i)(8765|dart|flutter|atlas|python)") {
            $interesting += [pscustomobject]@{
                DisplayName = $rule.DisplayName
                Name = $rule.Name
                Direction = $rule.Direction
                Action = $rule.Action
                Enabled = $rule.Enabled
                Ports = ($ports -join "; ")
                Applications = ($apps -join "; ")
            }
        }
    }
    $interesting
}

function Add-ObjectBlock {
    param(
        [System.Collections.Generic.List[string]]$Lines,
        [object]$Value
    )

    $text = ($Value | Format-List * | Out-String).TrimEnd()
    if ([string]::IsNullOrWhiteSpace($text)) {
        $Lines.Add("_No output._")
    } else {
        $Lines.Add('```')
        foreach ($line in ($text -split '\r?\n')) {
            $Lines.Add($line)
        }
        $Lines.Add('```')
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmm"
$reportDirectory = if ([System.IO.Path]::IsPathRooted($ReportDir)) {
    $ReportDir
} else {
    Join-Path (Get-Location) $ReportDir
}
New-Item -ItemType Directory -Force -Path $reportDirectory | Out-Null
$reportPath = Join-Path $reportDirectory "win_api_probe_$timestamp.md"

$icmpResults = @()
$tcpResults = @()
$httpResults = @()

foreach ($target in $Targets) {
    try {
        $icmp = Test-Connection -ComputerName $target -Count 2 -Quiet
        $icmpResults += New-CheckResult -Target $target -Port 0 -Probe "ICMP" -Succeeded $icmp -Detail "Test-Connection -Count 2 -Quiet"
    } catch {
        $icmpResults += New-CheckResult -Target $target -Port 0 -Probe "ICMP" -Succeeded $false -Detail $_.Exception.Message
    }

    foreach ($port in $Ports) {
        try {
            $tcp = Test-NetConnection -ComputerName $target -Port $port -WarningAction SilentlyContinue
            $sourceAddress = if ($tcp.SourceAddress -and $tcp.SourceAddress.IPAddress) {
                $tcp.SourceAddress.IPAddress
            } else {
                [string]$tcp.SourceAddress
            }
            $tcpResults += New-CheckResult -Target $target -Port $port -Probe "TCP" -Succeeded ([bool]$tcp.TcpTestSucceeded) -Detail "SourceAddress=$sourceAddress; InterfaceAlias=$($tcp.InterfaceAlias)"
        } catch {
            $tcpResults += New-CheckResult -Target $target -Port $port -Probe "TCP" -Succeeded $false -Detail $_.Exception.Message
        }

        $iwrResult = Invoke-IwrHealth -Target $target -Port $port -ProbeName "Invoke-WebRequest"
        $httpResults += $iwrResult
        $curlResult = Invoke-CurlHealth -Target $target -Port $port
        $httpResults += $curlResult

        if (-not $iwrResult.Succeeded -and $curlResult.Succeeded) {
            [System.Net.WebRequest]::DefaultWebProxy = $null
            $httpResults += Invoke-IwrHealth -Target $target -Port $port -ProbeName "Invoke-WebRequest (proxy disabled)"
        }
    }
}

try {
    $firewallProfiles = Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultOutboundAction
} catch {
    $firewallProfiles = [pscustomobject]@{ Error = $_.Exception.Message }
}
$interestingBlocks = Get-InterestingOutboundBlocks
try {
    $winHttpProxy = (& netsh.exe winhttp show proxy) -join "`n"
} catch {
    $winHttpProxy = "ERROR: $($_.Exception.Message)"
}
try {
    $internetProxy = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -Name ProxyEnable, ProxyServer -ErrorAction SilentlyContinue |
        Select-Object ProxyEnable, ProxyServer
} catch {
    $internetProxy = [pscustomobject]@{ Error = $_.Exception.Message }
}

$httpPass = @($httpResults | Where-Object { $_.Succeeded -and $_.StatusCode -eq 200 }).Count -gt 0
$status = if ($httpPass) { "PASS" } else { "FAIL" }

$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("# Windows job-api network probe")
$lines.Add("")
$lines.Add("- Timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$lines.Add("- Hostname: $(hostname)")
$lines.Add("- User: $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)")
$lines.Add("- Targets: $($Targets -join ', ')")
$lines.Add("- Ports: $($Ports -join ', ')")
$lines.Add("- Health path: $HealthPath")
$lines.Add("")
$lines.Add("## IP configuration")
Add-ObjectBlock -Lines $lines -Value ((ipconfig) -join "`n")
$lines.Add("")
$lines.Add("## ICMP probes")
Add-ObjectBlock -Lines $lines -Value $icmpResults
$lines.Add("")
$lines.Add("## TCP probes")
Add-ObjectBlock -Lines $lines -Value $tcpResults
$lines.Add("")
$lines.Add("## HTTP probes")
Add-ObjectBlock -Lines $lines -Value $httpResults
$lines.Add("")
$lines.Add("## H1 diagnostics: firewall and proxy")
$lines.Add("")
$lines.Add("### Firewall profiles")
Add-ObjectBlock -Lines $lines -Value $firewallProfiles
$lines.Add("")
$lines.Add("### Outbound block rules mentioning 8765, dart, flutter, atlas, or python")
if (@($interestingBlocks).Count -eq 0) {
    $lines.Add("_No matching enabled outbound block rules found._")
} else {
    Add-ObjectBlock -Lines $lines -Value $interestingBlocks
}
$lines.Add("")
$lines.Add("### WinHTTP proxy")
$lines.Add('```')
foreach ($line in ($winHttpProxy -split '\r?\n')) {
    $lines.Add($line)
}
$lines.Add('```')
$lines.Add("")
$lines.Add("### HKCU Internet Settings proxy")
Add-ObjectBlock -Lines $lines -Value $internetProxy
$lines.Add("")
$lines.Add("## H2 diagnostics: curl.exe versus Invoke-WebRequest")
$h2Summary = if (@($httpResults | Where-Object { $_.Probe -eq "curl.exe" -and $_.Succeeded }).Count -gt 0 -and @($httpResults | Where-Object { $_.Probe -eq "Invoke-WebRequest" -and $_.Succeeded }).Count -eq 0) {
    "curl.exe succeeded while Invoke-WebRequest failed; PowerShell proxy inheritance is indicated."
} elseif (@($httpResults | Where-Object { $_.Probe -eq "curl.exe" -and $_.Succeeded }).Count -gt 0 -and @($httpResults | Where-Object { $_.Probe -eq "Invoke-WebRequest" -and $_.Succeeded }).Count -gt 0) {
    "curl.exe and Invoke-WebRequest both succeeded; H2 is not indicated in this run."
} else {
    "curl.exe did not prove an HTTP path independent of Invoke-WebRequest in this run."
}
$lines.Add($h2Summary)
$lines.Add("")
$lines.Add("STATUS: $status")

Set-Content -LiteralPath $reportPath -Value $lines -Encoding ASCII
Write-Output $reportPath

if ($httpPass) {
    exit 0
}
exit 1
