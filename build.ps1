<#
.SYNOPSIS
    TriTTer development helper script.

.DESCRIPTION
    Activate the venv, run the app, or produce a single-file Windows
    executable with PyInstaller.

.PARAMETER Action
    run   – launch the app directly from source (default)
    build – compile dist\TriTTer.exe with PyInstaller
#>
param(
    [ValidateSet("run", "build", "buildfolder")]
    [string]$Action = "build"
)

$ErrorActionPreference = "Stop"
$Root        = $PSScriptRoot
$Venv        = Join-Path $Root ".venv"
$Python      = Join-Path $Venv "Scripts\python.exe"
$PyInstaller = Join-Path $Venv "Scripts\pyinstaller.exe"

# Activate venv
$ActivateScript = Join-Path $Venv "Scripts\Activate.ps1"
if (Test-Path $ActivateScript) {
    . $ActivateScript
 }

switch ($Action) {

    "run" {
        Write-Host "Starting TriTTer from source..." -ForegroundColor Cyan
        & $Python (Join-Path $Root "src\main.py")
    }

    "build" {
        Write-Host "Building TriTTer.exe (single file)..." -ForegroundColor Cyan

        Get-Process -Name "TriTTer" -ErrorAction SilentlyContinue | Stop-Process -Force
        $SpecFile = Join-Path $Root "TriTTer.spec"
        & $PyInstaller --clean --noconfirm $SpecFile

        $Exe = Join-Path $Root "dist\TriTTer.exe"
        if (Test-Path $Exe) {
            Write-Host "Build successful: $Exe" -ForegroundColor Green
        } else {
            Write-Error "Build failed - exe not found at $Exe"
        }
    }

    "buildfolder" {
        Write-Host "Building TriTTer folder (one-dir)..." -ForegroundColor Cyan

        $env:TRITTER_ONEFILE = "0"
        $SpecFile = Join-Path $Root "TriTTer.spec"
        & $PyInstaller --clean --noconfirm $SpecFile
        $env:TRITTER_ONEFILE = $null

        $Dir = Join-Path $Root "dist\TriTTer"
        if (Test-Path $Dir) {
            Write-Host "Build successful: $Dir" -ForegroundColor Green
        } else {
            Write-Error "Build failed - folder not found at $Dir"
        }
    }
}
