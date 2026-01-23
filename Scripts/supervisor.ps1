# DCSServerBot Supervisor Script
# This script monitors the bot process and handles restart requests
#
# Usage: powershell -ExecutionPolicy Bypass -File supervisor.ps1
#
# The script:
# 1. Starts the bot process
# 2. Monitors for a restart signal file (.restart_requested)
# 3. When signal detected, gracefully stops and restarts the bot
# 4. Loops indefinitely until manually stopped (Ctrl+C)

param(
    [string]$BotDir = "C:\Users\Administrator\github\DCSServerBot",
    [string]$VenvPath = "C:\Users\Administrator\.dcssb",
    [int]$RestartDelay = 5
)

$RestartSignalFile = Join-Path $BotDir ".restart_requested"
$ShutdownSignalFile = Join-Path $BotDir ".shutdown_requested"

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] $Message"
}

function Start-Bot {
    Write-Log "Starting DCSServerBot..."

    # Activate venv and run the bot
    $env:VIRTUAL_ENV = $VenvPath
    $env:PATH = "$VenvPath\Scripts;$env:PATH"

    $process = Start-Process -FilePath "$VenvPath\Scripts\python.exe" `
        -ArgumentList "run.py" `
        -WorkingDirectory $BotDir `
        -PassThru `
        -NoNewWindow

    Write-Log "Bot started with PID: $($process.Id)"
    return $process
}

function Stop-Bot {
    param([System.Diagnostics.Process]$Process)

    if ($Process -and !$Process.HasExited) {
        Write-Log "Stopping bot (PID: $($Process.Id))..."

        # Send Ctrl+C signal for graceful shutdown
        $Process.CloseMainWindow() | Out-Null

        # Wait up to 30 seconds for graceful shutdown
        $waited = 0
        while (!$Process.HasExited -and $waited -lt 30) {
            Start-Sleep -Seconds 1
            $waited++
        }

        # Force kill if still running
        if (!$Process.HasExited) {
            Write-Log "Force killing bot process..."
            $Process.Kill()
        }

        Write-Log "Bot stopped."
    }
}

# Clean up any stale signal files
if (Test-Path $RestartSignalFile) { Remove-Item $RestartSignalFile -Force }
if (Test-Path $ShutdownSignalFile) { Remove-Item $ShutdownSignalFile -Force }

Write-Log "=== DCSServerBot Supervisor Started ==="
Write-Log "Bot directory: $BotDir"
Write-Log "Venv path: $VenvPath"
Write-Log "Press Ctrl+C to stop the supervisor"

$running = $true
$botProcess = $null

try {
    while ($running) {
        # Start the bot if not running
        if ($null -eq $botProcess -or $botProcess.HasExited) {
            if ($null -ne $botProcess -and $botProcess.HasExited) {
                Write-Log "Bot process exited with code: $($botProcess.ExitCode)"
                Write-Log "Restarting in $RestartDelay seconds..."
                Start-Sleep -Seconds $RestartDelay
            }
            $botProcess = Start-Bot
        }

        # Check for restart signal
        if (Test-Path $RestartSignalFile) {
            Write-Log "Restart signal detected!"
            Remove-Item $RestartSignalFile -Force
            Stop-Bot -Process $botProcess
            $botProcess = $null
            Write-Log "Restarting in $RestartDelay seconds..."
            Start-Sleep -Seconds $RestartDelay
            continue
        }

        # Check for shutdown signal
        if (Test-Path $ShutdownSignalFile) {
            Write-Log "Shutdown signal detected!"
            Remove-Item $ShutdownSignalFile -Force
            $running = $false
            continue
        }

        # Brief sleep before next check
        Start-Sleep -Seconds 1
    }
}
finally {
    Stop-Bot -Process $botProcess
    Write-Log "=== DCSServerBot Supervisor Stopped ==="
}
