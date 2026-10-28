<#
.SYNOPSIS
  Lance le mode démo local de « Prepare data for DATEV », sans Docker et sans mode développement.

.DESCRIPTION
  1. vérifie que PostgreSQL répond et met la base à jour (uie db upgrade) ;
  2. construit le site en mode production (next build, profil « demo » : seuls les use cases validés) ;
  3. lance l'API sans rechargement automatique, puis le site (next start) ;
  4. prépare le dossier de démo (uie demo prepare) ;
  5. vérifie que la page répond, puis affiche l'adresse à ouvrir.
  Arrêt : scripts\demo-stop.ps1. Journaux et numéros de processus : var\demo\.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\demo-start.ps1
  powershell -ExecutionPolicy Bypass -File scripts\demo-start.ps1 -SkipBuild     # relance rapide
#>
param(
    [string]$Org = "go45-development",
    [string]$Answers = "temp/demo_answers.yaml",
    [int]$ApiPort = 8000,
    [int]$WebPort = 3000,
    [switch]$SkipBuild,     # réutilise la dernière construction démo si elle correspond au même commit et au même port d'API
    [switch]$SkipPrepare    # ne remet pas le dossier de démo à zéro
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Web = Join-Path $Root "apps\web"
$State = Join-Path $Root "var\demo"
$PidFile = Join-Path $State "pids.json"
$BuildMarker = Join-Path $State "build.json"
$ApiUrl = "http://127.0.0.1:$ApiPort"
New-Item -ItemType Directory -Force -Path $State | Out-Null
Set-Location $Root

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   OK  $text" -ForegroundColor Green }
function Fail($text) {
    Write-Host "   ERREUR  $text" -ForegroundColor Red
    exit 1
}

# Statut HTTP d'une adresse (0 = aucune réponse) ; Windows PowerShell lève une exception sur 4xx / 5xx.
function HttpStatus($url) {
    try {
        return [int](Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10).StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

function WaitFor($url, [int[]]$expected, [int]$seconds, $what) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $code = HttpStatus $url
        if ($expected -contains $code) { return $code }
        Start-Sleep -Milliseconds 700
    }
    Fail "$what ne répond pas ($url) après $seconds s. Journaux : $State"
}

# Mémoire (Mo) d'un processus et de ses descendants
function TreeMemory([int]$rootPid) {
    $all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, WorkingSetSize, PrivatePageCount
    $ids = @($rootPid); $i = 0
    while ($i -lt $ids.Count) {
        $ids += @($all | Where-Object { $_.ParentProcessId -eq $ids[$i] } | ForEach-Object { [int]$_.ProcessId })
        $i++
    }
    $mine = $all | Where-Object { $ids -contains [int]$_.ProcessId }
    $ws = ($mine | Measure-Object WorkingSetSize -Sum).Sum / 1MB
    $priv = ($mine | Measure-Object PrivatePageCount -Sum).Sum / 1MB
    return "{0:N0} Mo (privée {1:N0} Mo, {2} processus)" -f $ws, $priv, @($mine).Count
}

# ---------------------------------------------------------------- 0. rien ne tourne déjà
Step "Contrôles"
if (Test-Path $PidFile) {
    $old = Get-Content $PidFile -Raw | ConvertFrom-Json
    if ((Get-Process -Id $old.api, $old.web -ErrorAction SilentlyContinue)) {
        Fail "La démo tourne déjà. Lancer d'abord scripts\demo-stop.ps1."
    }
    Remove-Item $PidFile
}
foreach ($port in @($ApiPort, $WebPort)) {
    $busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($busy) {
        $name = (Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Fail "Le port $port est déjà pris (processus $name, PID $($busy.OwningProcess)). Fermer le mode développement (pnpm dev, uie api serve) puis relancer."
    }
}
foreach ($cmd in @("uv", "node", "pnpm")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Fail "Commande introuvable : $cmd" }
}
if (-not $SkipPrepare -and -not (Test-Path $Answers)) { Fail "Fichier de réponses introuvable : $Answers" }
Ok "ports $ApiPort et $WebPort libres"

# ---------------------------------------------------------------- 1. PostgreSQL et migrations
Step "1. Base de données"
$dbUrl = $env:UIE_DATABASE_URL
if (-not $dbUrl -and (Test-Path ".env")) {
    $line = Get-Content ".env" | Where-Object { $_ -match '^\s*UIE_DATABASE_URL\s*=' } | Select-Object -First 1
    if ($line) { $dbUrl = ($line -split "=", 2)[1].Trim().Trim('"') }
}
if (-not $dbUrl -or $dbUrl -notmatch '@(?<host>[^:/]+)(:(?<port>\d+))?/(?<db>[^?]+)') {
    Fail "UIE_DATABASE_URL absente ou illisible (.env)"
}
$dbHost = $Matches.host; $dbPort = if ($Matches.port) { [int]$Matches.port } else { 5432 }; $dbName = $Matches.db
$tcp = New-Object System.Net.Sockets.TcpClient
try {
    $reached = $tcp.ConnectAsync($dbHost, $dbPort).Wait(3000) -and $tcp.Connected
} catch { $reached = $false } finally { $tcp.Dispose() }
if (-not $reached) { Fail "PostgreSQL ne répond pas sur ${dbHost}:$dbPort. Démarrer le service PostgreSQL local." }
Ok "PostgreSQL répond sur ${dbHost}:$dbPort (base $dbName)"
& uv run --no-sync uie db upgrade
if ($LASTEXITCODE -ne 0) { Fail "uie db upgrade a échoué" }
Ok "base $dbName à jour"

# ---------------------------------------------------------------- 2. site en mode production
Step "2. Construction du site (next build, profil demo)"
$commit = (& git rev-parse HEAD).Trim()
$dirty = [bool](& git status --porcelain -- apps/web)
$marker = if (Test-Path $BuildMarker) { Get-Content $BuildMarker -Raw | ConvertFrom-Json } else { $null }
$buildIdFile = Join-Path $Web ".next\BUILD_ID"
$buildId = if (Test-Path $buildIdFile) { (Get-Content $buildIdFile -Raw).Trim() } else { "" }
# même commit, rien de modifié, même API, et le build présent est bien CELUI de la démo (un `pnpm build` normal le
# remplace sans profil : son BUILD_ID diffère)
$reuse = $SkipBuild -and $marker -and $marker.commit -eq $commit -and -not $marker.dirty -and -not $dirty `
    -and $marker.api -eq $ApiUrl -and $buildId -and $marker.buildId -eq $buildId
if ($reuse) {
    Ok "construction démo réutilisée (commit $($commit.Substring(0, 7)))"
} else {
    if ($SkipBuild) { Write-Host "   construction démo absente ou périmée : reconstruction" -ForegroundColor Yellow }
    $env:NEXT_PUBLIC_UIE_PROFILE = "demo"
    $env:UIE_API_URL = $ApiUrl
    $env:NEXT_TELEMETRY_DISABLED = "1"
    $started = Get-Date
    Push-Location $Web
    try { & pnpm exec next build } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { Fail "next build a échoué" }
    $buildId = (Get-Content $buildIdFile -Raw).Trim()
    @{ commit = $commit; dirty = $dirty; api = $ApiUrl; profile = "demo"; buildId = $buildId; built = (Get-Date).ToString("s") } |
        ConvertTo-Json | Set-Content -Encoding utf8 $BuildMarker
    Ok ("site construit en {0:N0} s" -f ((Get-Date) - $started).TotalSeconds)
}

# ---------------------------------------------------------------- 3. API puis site
Step "3. Lancement de l'API et du site"
$env:UIE_API_URL = $ApiUrl
$api = Start-Process -FilePath (Get-Command uv).Source `
    -ArgumentList "run", "--no-sync", "uie", "api", "serve", "--port", "$ApiPort" `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $State "api.log") -RedirectStandardError (Join-Path $State "api.err.log")
$web = Start-Process -FilePath (Get-Command node).Source `
    -ArgumentList "node_modules\next\dist\bin\next", "start", "--port", "$WebPort" `
    -WorkingDirectory $Web -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $State "web.log") -RedirectStandardError (Join-Path $State "web.err.log")
@{
    api = $api.Id; apiStart = $api.StartTime.ToString("o"); web = $web.Id; webStart = $web.StartTime.ToString("o")
    apiPort = $ApiPort; webPort = $WebPort
} | ConvertTo-Json | Set-Content -Encoding utf8 $PidFile
WaitFor "$ApiUrl/auth/me" @(401) 90 "L'API" | Out-Null
Ok "API sur $ApiUrl (sans rechargement automatique, PID $($api.Id))"

# ---------------------------------------------------------------- 4. dossier de démo
Step "4. Préparation du dossier de démo ($Org)"
if ($SkipPrepare) {
    Ok "sauté (-SkipPrepare)"
} else {
    & uv run --no-sync uie demo prepare --org $Org --answers $Answers --yes
    if ($LASTEXITCODE -ne 0) {
        Fail "uie demo prepare a échoué (rien n'est enregistré). Les serveurs restent lancés : corriger puis relancer, ou scripts\demo-stop.ps1."
    }
}

# ---------------------------------------------------------------- 5. la page répond
Step "5. Vérification de la page"
WaitFor "http://127.0.0.1:$WebPort/portal/prepare-datev" @(200) 90 "Le site" | Out-Null
$proxy = HttpStatus "http://127.0.0.1:$WebPort/uie-api/auth/me"
if ($proxy -ne 401) { Fail "Le site ne joint pas l'API par /uie-api (statut $proxy)" }
Ok "page Prepare data for DATEV : 200 ; site → API : OK"

Step "Mémoire"
Write-Host "   API  : $(TreeMemory $api.Id)"
Write-Host "   Site : $(TreeMemory $web.Id)"

Write-Host ""
Write-Host "Démo prête : http://localhost:$WebPort/portal/prepare-datev" -ForegroundColor Green
Write-Host "Arrêt : powershell -ExecutionPolicy Bypass -File scripts\demo-stop.ps1"
