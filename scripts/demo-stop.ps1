<#
.SYNOPSIS
  Arrête l'API et le site lancés par scripts\demo-start.ps1.

.DESCRIPTION
  Arrête les processus enregistrés dans var\demo\pids.json (et leurs descendants), après avoir vérifié qu'il
  s'agit bien des mêmes (heure de démarrage). Sans ce fichier, n'arrête que les processus qui écoutent sur les
  ports de la démo ET dont la ligne de commande est celle de la démo (next start, uie api serve sans --reload) :
  jamais un « next dev » ni une API lancée en mode développement.
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

# filet : un serveur de démo resté sur les ports (fichier perdu), reconnu par sa ligne de commande
foreach ($port in @($ApiPort, $WebPort)) {
    $listen = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listen) { continue }
    $ids = @([int]$listen.OwningProcess)
    $cur = Get-CimInstance Win32_Process -Filter "ProcessId = $($ids[0])"
    while ($cur -and $cur.ParentProcessId) {   # remonte jusqu'au processus lancé par demo-start (uv, node)
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($cur.ParentProcessId)"
        if (-not $parent -or $parent.CommandLine -notmatch 'next|uie|uvicorn') { break }
        $ids += [int]$parent.ProcessId; $cur = $parent
    }
    $line = ($ids | ForEach-Object { (Get-CimInstance Win32_Process -Filter "ProcessId = $_").CommandLine }) -join " "
    $isDemo = ($line -match 'next(\.js)?["'' ]+start' -or $line -match 'uie["'' ]+api["'' ]+serve') `
        -and $line -notmatch 'next(\.js)?["'' ]+dev' -and $line -notmatch '--reload'
    if ($isDemo) {
        KillTree $ids[-1] "serveur de démo sur le port $port"; $stopped++
    } else {
        Write-Host "   port $port : un autre programme écoute (mode développement ?), laissé tel quel" -ForegroundColor Yellow
    }
}

Start-Sleep -Seconds 1
$left = @($ApiPort, $WebPort) | Where-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue }
if ($stopped -eq 0) { Write-Host "Aucune démo en cours." }
if ($left) { Write-Host "Ports encore pris : $($left -join ', ')" -ForegroundColor Yellow } else { Write-Host "Démo arrêtée." -ForegroundColor Green }
