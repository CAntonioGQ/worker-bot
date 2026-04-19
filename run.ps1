# run.ps1 — wrapper que relanza el bot si se cae.
# Uso manual:  powershell -ExecutionPolicy Bypass -File run.ps1
# Para autoarranque ver sección "Despliegue local" del README.

$ErrorActionPreference = "Continue"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$RestartDelay = 10

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] Iniciando worker-bot..."

    & uv run python main.py
    $code = $LASTEXITCODE

    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] Bot terminó con código $code. Reintentando en $RestartDelay s..."
    Start-Sleep -Seconds $RestartDelay
}
