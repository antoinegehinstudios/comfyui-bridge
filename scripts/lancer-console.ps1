# Lanceur de la console ComfyUI Bridge.
#
# Un double-clic doit suffire : si la passerelle repond deja, on ouvre
# simplement la console ; sinon on la demarre (detachee, elle survit a la
# fermeture de cette fenetre), on attend qu'elle reponde vraiment, puis on
# ouvre le navigateur. Le moteur ComfyUI est amene par la passerelle
# elle-meme (attache a celui qui repond deja, sinon lance).
#
# Aucune sortie n'est inventee : ce qui s'affiche vient d'une reponse HTTP
# reelle ou du journal de demarrage.

$ErrorActionPreference = 'Stop'
$racine  = Split-Path -Parent $PSScriptRoot
$url     = 'http://127.0.0.1:8077'
$journal = Join-Path $racine '_data\bridge.err.log'

# Sur un port ferme, Invoke-WebRequest met 2 s a echouer (resolution/proxy) :
# la boucle d'attente y passait 30 fois plus de temps que le demarrage reel.
# On teste d'abord si le port ecoute, ce qui est immediat, et on ne fait la
# vraie requete que lorsqu'il y a quelqu'un au bout.
function PortOuvert {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $essai = $client.BeginConnect('127.0.0.1', 8077, $null, $null)
        if (-not $essai.AsyncWaitHandle.WaitOne(300)) { return $false }
        $client.EndConnect($essai)
        return $true
    } catch { return $false } finally { $client.Close() }
}

function Repond {
    if (-not (PortOuvert)) { return $false }
    try {
        $r = Invoke-WebRequest -Uri "$url/healthz" -TimeoutSec 5 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Ouvrir { Start-Process "$url/ui" }

if (Repond) {
    Write-Host "Console deja en service." -ForegroundColor Green
    Ouvrir
    exit 0
}

Write-Host "Demarrage de la passerelle..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path (Join-Path $racine '_data') | Out-Null

$python = 'python'
$venv = Join-Path $racine '.venv\Scripts\python.exe'
if (Test-Path $venv) { $python = $venv }

$env:PYTHONUTF8 = '1'
Start-Process -FilePath $python `
    -ArgumentList '-m','uvicorn','comfyui_bridge.api.main:app','--host','127.0.0.1','--port','8077' `
    -WorkingDirectory $racine -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $racine '_data\bridge.out.log') `
    -RedirectStandardError  $journal

# Le premier demarrage peut lancer ComfyUI : plusieurs minutes, pas quelques secondes.
$limite = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $limite) {
    Start-Sleep -Milliseconds 500
    if (Repond) {
        Write-Host "En service." -ForegroundColor Green
        Ouvrir
        exit 0
    }
}

# Rien d'invente : on montre la vraie fin du journal.
$fin = if (Test-Path $journal) { Get-Content $journal -Tail 15 -ErrorAction SilentlyContinue } else { @() }
$texte = "La passerelle n'a pas repondu sur $url apres 5 minutes.`n`nFin du journal :`n" + ($fin -join "`n")
Write-Host $texte -ForegroundColor Red
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show($texte, 'ComfyUI Bridge', 'OK', 'Error') | Out-Null
exit 1
