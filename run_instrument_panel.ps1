param(
    [switch]$NoSimulator,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"

$Root = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

Set-Location $Root

$PanelPath = Join-Path $Root "instrument_panel.html"
$SimulatorPath = Join-Path $Root "mcu_simulator.py"
$HostName = "127.0.0.1"
$Port = 7897
$StartedSimulator = $false
$SimulatorProcess = $null

function Test-PortOpen {
    param(
        [string]$HostName,
        [int]$Port
    )

    $Client = New-Object System.Net.Sockets.TcpClient
    try {
        $AsyncResult = $Client.BeginConnect($HostName, $Port, $null, $null)
        $Connected = $AsyncResult.AsyncWaitHandle.WaitOne(250, $false)
        if (-not $Connected) {
            return $false
        }
        $Client.EndConnect($AsyncResult)
        return $true
    } catch {
        return $false
    } finally {
        $Client.Close()
    }
}

function Get-PythonCommand {
    $Candidates = @(
        @{ Name = "python.exe"; Args = @() },
        @{ Name = "python3.exe"; Args = @() },
        @{ Name = "py.exe"; Args = @("-3") }
    )

    foreach ($Candidate in $Candidates) {
        $Command = Get-Command $Candidate.Name -ErrorAction SilentlyContinue
        if ($Command) {
            return @{
                File = $Command.Source
                Args = $Candidate.Args
            }
        }
    }

    return $null
}

function Get-BrowserPath {
    $Paths = @()
    foreach ($Base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if ($Base) {
            $Paths += Join-Path $Base "Microsoft\Edge\Application\msedge.exe"
            $Paths += Join-Path $Base "Google\Chrome\Application\chrome.exe"
        }
    }

    foreach ($Path in $Paths) {
        if ($Path -and (Test-Path $Path)) {
            return $Path
        }
    }

    foreach ($Name in @("msedge.exe", "chrome.exe")) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) {
            return $Command.Source
        }
    }

    return $null
}

try {
    if (-not (Test-Path $PanelPath)) {
        throw "instrument_panel.html was not found."
    }

    if (-not $NoSimulator) {
        if (Test-PortOpen -HostName $HostName -Port $Port) {
            Write-Host "Simulator port 7897 is already open. Reusing it."
        } elseif (Test-Path $SimulatorPath) {
            $Python = Get-PythonCommand
            if ($Python) {
                $StdoutLog = Join-Path $Root "mcu_simulator.log"
                $StderrLog = Join-Path $Root "mcu_simulator.err.log"
                $Arguments = @()
                $Arguments += $Python.Args
                $Arguments += $SimulatorPath

                $SimulatorProcess = Start-Process `
                    -FilePath $Python.File `
                    -ArgumentList $Arguments `
                    -WorkingDirectory $Root `
                    -RedirectStandardOutput $StdoutLog `
                    -RedirectStandardError $StderrLog `
                    -WindowStyle Hidden `
                    -PassThru

                $StartedSimulator = $true
                Set-Content -Path (Join-Path $Root ".mcu_simulator.pid") -Value $SimulatorProcess.Id -Encoding ASCII

                for ($Index = 0; $Index -lt 30; $Index++) {
                    if (Test-PortOpen -HostName $HostName -Port $Port) {
                        break
                    }
                    Start-Sleep -Milliseconds 200
                }

                if (Test-PortOpen -HostName $HostName -Port $Port) {
                    Write-Host "Simulator started on 127.0.0.1:7897."
                } else {
                    Write-Warning "Simulator did not open port 7897. Check mcu_simulator.err.log."
                }
            } else {
                Write-Warning "Python was not found. Opening the instrument panel only."
            }
        } else {
            Write-Warning "mcu_simulator.py was not found. Opening the instrument panel only."
        }
    }

    $PanelUri = ([System.Uri]$PanelPath).AbsoluteUri
    $Browser = Get-BrowserPath
    if ($Browser) {
        Start-Process -FilePath $Browser -ArgumentList @($PanelUri)
    } else {
        Start-Process -FilePath $PanelPath
    }

    Write-Host ""
    Write-Host "Instrument panel opened."
    Write-Host "For simulator demo, click: Connect Simulator."
    Write-Host "For real board demo, click: Connect DAPmini Serial."

    if (-not $NoWait) {
        Write-Host ""
        [void](Read-Host "Press Enter here to stop the simulator started by this launcher and exit")
    }
} finally {
    if ($StartedSimulator -and $SimulatorProcess) {
        try {
            if (-not $SimulatorProcess.HasExited) {
                Stop-Process -Id $SimulatorProcess.Id -Force
                Write-Host "Simulator stopped."
            }
        } catch {
            Write-Warning "Could not stop simulator process $($SimulatorProcess.Id)."
        }

        Remove-Item -Path (Join-Path $Root ".mcu_simulator.pid") -ErrorAction SilentlyContinue
    }
}
