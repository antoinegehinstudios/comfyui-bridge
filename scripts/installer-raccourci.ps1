# Pose (ou remet a jour) le raccourci "ComfyUI Bridge" sur le Bureau.
# Rejouable : relancer ce script apres un deplacement du projet suffit.

$ErrorActionPreference = 'Stop'
$racine   = Split-Path -Parent $PSScriptRoot
$lanceur  = Join-Path $PSScriptRoot 'lancer-console.ps1'
$icone    = Join-Path $PSScriptRoot 'cortex-bridge.ico'
# GetFolderPath suit la redirection OneDrive, contrairement a "$HOME\Desktop".
$bureau   = [Environment]::GetFolderPath('Desktop')
$raccourci = Join-Path $bureau 'ComfyUI Bridge.lnk'

if (-not (Test-Path $lanceur)) { throw "lanceur introuvable : $lanceur" }

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($raccourci)
$lnk.TargetPath = (Get-Command powershell.exe).Source
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$lanceur`""
$lnk.WorkingDirectory = $racine
$lnk.Description = 'Demarre la passerelle ComfyUI et ouvre la console'
if (Test-Path $icone) { $lnk.IconLocation = "$icone,0" }
$lnk.Save()

Write-Host "Raccourci pose : $raccourci"
