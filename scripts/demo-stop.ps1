<#
.SYNOPSIS
  Arrête l'API et le site lancés par scripts\demo-start.ps1.

.DESCRIPTION
  Arrête les processus enregistrés dans var\demo\pids.json (et leurs descendants), après avoir vérifié qu'il
  s'agit bien des mêmes (heure de démarrage). Aucun autre processus n'est arrêté : sans ce fichier, le script
  indique seulement ce qui écoute encore sur les ports de la démo (jamais deviné : une API lancée à la main sans
  --reload a la même ligne de commande que celle de la démo).
#>
param(
    [int]$ApiPort = 8000,
    [int]$WebPort = 3000
)

$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root "var\demo\pids.json"

function KillTree([int]$id, $what) {
    & taskkill.exe /PID $id /T /F 2>&1 | Out-Null
    Write-Host "   arrêté : $what (PID $id)" -ForegroundColor Green
}

$stopped = 0
if (Test-Path $PidFile) {
    $saved = Get-Content $PidFile -Raw | ConvertFrom-Json
    $ApiPort = [int]$saved.apiPort; $WebPort = [int]$saved.webPort
    foreach ($entry in @(@("API", $saved.api, $saved.apiStart), @("site", $saved.web, $saved.webStart))) {
        $proc = Get-Process -Id $entry[1] -ErrorAction SilentlyContinue
        if (-not $proc) { Write-Host "   $($entry[0]) : déjà arrêté"; continue }
        if ([math]::Abs(($proc.StartTime - [datetime]$entry[2]).TotalSeconds) -gt 2) {
            Write-Host "   $($entry[0]) : le PID $($entry[1]) appartient à un autre programme, laissé tel quel" -ForegroundColor Yellow
            continue
        }
        KillTree $entry[1] $entry[0]; $stopped++
    }
    Remove-Item $PidFile
}

if ($stopped -gt 0) { Start-Sleep -Seconds 1 }   # le temps de libérer les ports

# sans fichier de PID (perdu), rien n'est arrêté : une API ou un site lancés à la main ont la même ligne de commande
# que ceux de la démo. Les processus qui écoutent encore sur les ports sont seulement indiqués.
foreach ($port in @($ApiPort, $WebPort)) {
    $listen = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listen) { continue }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($listen.OwningProcess)"
    $line = "$($proc.CommandLine)"
    Write-Host "   port $port pris par PID $($listen.OwningProcess) : $($line.Substring(0, [math]::Min(160, $line.Length)))" -ForegroundColor Yellow
    Write-Host "   s'il s'agit de la démo : taskkill /PID $($listen.OwningProcess) /T /F" -ForegroundColor Yellow
}

$left = @($ApiPort, $WebPort) | Where-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue }
if ($left) { Write-Host "Ports encore pris : $($left -join ', ')" -ForegroundColor Yellow }
elseif ($stopped -eq 0) { Write-Host "Aucune démo en cours." }
else { Write-Host "Démo arrêtée." -ForegroundColor Green }
