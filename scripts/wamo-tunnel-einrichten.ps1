# Richtet auf diesem PC den WAMO-Tunnel ein (Betriebsart Büro), nur für den aktuellen Benutzer, ohne Adminrechte.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wamo-tunnel-einrichten.ps1 [-Starten] [-TokenNeu]
#
# Was das Skript anlegt (Rückweg: scripts\wamo-tunnel-entfernen.ps1 [-Alles]):
#   %LOCALAPPDATA%\WAMO                 Ordner nur für diesen Benutzer (Vererbung aus, nur eine Regel)
#   %LOCALAPPDATA%\WAMO\host-token      Bearer-Token des Hosts, 256 Bit, base64url (wird nie angezeigt)
#   %USERPROFILE%\.ssh\id_ed25519_wamo_tunnel(.pub)   eigener Schlüssel nur für den Tunnel, ohne Passphrase
#   %USERPROFILE%\.ssh\known_hosts_wamo_tunnel        Hostschlüssel von Sokrates-2 (Fingerabdruck fest geprüft)
#   Rechte von scripts und host          Vererbung aus: dieser Benutzer, SYSTEM, Administratoren Vollzugriff, Benutzer
#                                       nur Lesen (vorher erbten beide "Authentifizierte Benutzer: Ändern" vom Laufwerk)
#   Geplante Aufgabe "WAMO Tunnel"      bei Anmeldung dieses Benutzers, startet scripts\wamo-tunnel.ps1 versteckt
#   -TokenNeu startet einen laufenden Überwacher neu (der Host liest das Token nur beim Start)
# Danach auf Sokrates-2 (Schlüssel) und für den Kern (Token) weiter, siehe Ausgabe am Ende.
param(
  [switch]$Starten,
  [switch]$TokenNeu
)

Set-StrictMode -Version 3
$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Skript = Join-Path $PSScriptRoot "wamo-tunnel.ps1"
$Daten = Join-Path $env:LOCALAPPDATA "WAMO"
$TokenDatei = Join-Path $Daten "host-token"
$SshDir = Join-Path $env:USERPROFILE ".ssh"
$Schluessel = Join-Path $SshDir "id_ed25519_wamo_tunnel"
$KnownHosts = Join-Path $SshDir "known_hosts_wamo_tunnel"
$KnownHostsAuftraege = Join-Path $SshDir "known_hosts_wamo_jobs"
$ServerIp = "192.168.178.136"
# Hostschlüssel von Sokrates-2 (ED25519), geprüft am 19.10.2026 gegen /etc/ssh/ssh_host_ed25519_key.pub
$ErwarteterFingerabdruck = "SHA256:FI+Dtt+vY0zJgLOwu7IajO50ywDaPwznZ9UgwJLN4lQ"
$Aufgabe = "WAMO Tunnel"
$SshKeygen = Join-Path $env:SystemRoot "System32\OpenSSH\ssh-keygen.exe"
$SshKeyscan = Join-Path $env:SystemRoot "System32\OpenSSH\ssh-keyscan.exe"

function NurIch([string]$Pfad, [bool]$Ordner) {
  # Zugriff nur für den angemeldeten Benutzer: Vererbung aus, alle Regeln weg, eine Regel Vollzugriff
  # Nur die DACL schreiben (Set-Acl schriebe auch Besitzer und SACL, dafür fehlen ohne Adminrechte die Privilegien)
  $ich = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
  $acl = if ($Ordner) { New-Object System.Security.AccessControl.DirectorySecurity } else { New-Object System.Security.AccessControl.FileSecurity }
  $acl.SetAccessRuleProtection($true, $false)
  $vererbung = if ($Ordner) { "ContainerInherit, ObjectInherit" } else { "None" }
  $regel = New-Object System.Security.AccessControl.FileSystemAccessRule($ich, "FullControl", $vererbung, "None", "Allow")
  $acl.AddAccessRule($regel)
  (Get-Item -LiteralPath $Pfad -Force).SetAccessControl($acl)
}

Write-Host "== 1. Ordner $Daten (nur für $env:USERNAME)"
New-Item -ItemType Directory -Force -Path $Daten | Out-Null
NurIch $Daten $true
New-Item -ItemType Directory -Force -Path (Join-Path $Daten "logs") | Out-Null

Write-Host "== 2. Host-Token"
if ($TokenNeu -or -not (Test-Path $TokenDatei)) {
  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $rng.GetBytes($bytes)
  $rng.Dispose()
  $token = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
  [System.IO.File]::WriteAllText($TokenDatei, $token, (New-Object System.Text.ASCIIEncoding))
  Remove-Variable token, bytes
  Write-Host "  neues Token erzeugt (nicht angezeigt). Danach: python scripts\wamo_kern_sokrates2.py token"
} else {
  Write-Host "  vorhanden, unverändert (neu mit -TokenNeu)"
}
NurIch $TokenDatei $false

Write-Host "== 3. SSH-Schlüssel nur für den Tunnel"
New-Item -ItemType Directory -Force -Path $SshDir | Out-Null
if (-not (Test-Path $Schluessel)) {
  # '""' übergibt eine leere Passphrase (Windows PowerShell 5.1 verwirft sonst das leere Argument)
  & $SshKeygen -q -t ed25519 -N '""' -C "wamo-tunnel@$env:COMPUTERNAME" -f $Schluessel
  if ($LASTEXITCODE -ne 0) { throw "ssh-keygen fehlgeschlagen" }
  Write-Host "  neu: $Schluessel"
} else {
  Write-Host "  vorhanden: $Schluessel"
}
# Die Rechte des privaten Schlüssels setzt ssh-keygen selbst (ssh verweigert sonst den Schlüssel); hier nur prüfen,
# dass kein normales Konto außer diesem Benutzer Zugriff hat (SYSTEM und Administratoren sind für ssh zulässig).
$erlaubt = @([System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value, "S-1-5-18", "S-1-5-32-544")
foreach ($regel in (Get-Acl $Schluessel).Access) {
  $sid = $regel.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
  if ($regel.AccessControlType -eq "Allow" -and $erlaubt -notcontains $sid) { throw "Schlüssel ist für $($regel.IdentityReference) lesbar, Abbruch." }
}

Write-Host "== 4. Hostschlüssel von Sokrates-2"
$zeile = $null
if (Test-Path $KnownHostsAuftraege) {
  $zeile = Get-Content $KnownHostsAuftraege | Where-Object { $_ -match "^$([regex]::Escape($ServerIp)) ssh-ed25519 " } | Select-Object -First 1
}
if (-not $zeile) {
  $zeile = & $SshKeyscan -t ed25519 $ServerIp 2>$null | Where-Object { $_ -match "^$([regex]::Escape($ServerIp)) ssh-ed25519 " } | Select-Object -First 1
}
if (-not $zeile) { throw "Hostschlüssel von $ServerIp nicht gefunden (Büro-LAN?)" }
$tmp = "$KnownHosts.neu"
Set-Content -Path $tmp -Value $zeile -Encoding ascii
$fp = (& $SshKeygen -l -f $tmp) -split " " | Select-Object -Index 1
if ($fp -ne $ErwarteterFingerabdruck) {
  Remove-Item -Force $tmp
  throw "Hostschlüssel von $ServerIp passt nicht ($fp statt $ErwarteterFingerabdruck). Abbruch."
}
Move-Item -Force $tmp $KnownHosts
Write-Host "  $KnownHosts ($fp)"

Write-Host "== 5. Schreibrechte auf scripts und host (die Aufgabe startet von dort Code mit Token und Schlüssel)"
# Das Laufwerk vererbt "Authentifizierte Benutzer: Ändern". Für die beiden Ordner gilt danach: dieser Benutzer und
# SYSTEM und Administratoren Vollzugriff, Benutzer nur Lesen und Ausführen. Der Überwacher prüft das bei jedem Start.
$ich = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach ($ordner in @((Join-Path $Repo "scripts"), (Join-Path $Repo "host"))) {
  # Erst alle Einträge darunter auf "erbt vom Ordner" zurücksetzen, dann NUR den Ordner selbst härten (ohne /T):
  # /inheritance:r mit /T entfernte sonst auf jeder Datei die Vererbung, und (OI)(CI)-Rechte gelten für Dateien nicht
  # (Ergebnis wäre eine leere DACL, die Dateien wären für niemanden mehr lesbar).
  & icacls $ordner /reset /T /C /Q | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls /reset für $ordner fehlgeschlagen" }
  & icacls $ordner /inheritance:r /grant:r "*${ich}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" /C /Q | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls für $ordner fehlgeschlagen" }
  $leer = @(Get-ChildItem -LiteralPath $ordner -Recurse -Force | Where-Object { (Get-Acl -LiteralPath $_.FullName).Access.Count -eq 0 })
  if ($leer.Count -gt 0) { throw "$($leer.Count) Einträge in $ordner ohne Zugriffsregeln (icacls $ordner /reset /T)" }
  Write-Host "  $ordner gehärtet (Dateien erben)"
}

Write-Host "== 6. Geplante Aufgabe '$Aufgabe' (bei Anmeldung, nur $env:USERNAME)"
$konto = "$env:USERDOMAIN\$env:USERNAME"
$aktion = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Skript`"" `
  -WorkingDirectory $Repo
$ausloeser = New-ScheduledTaskTrigger -AtLogOn -User $konto
$prinzipal = New-ScheduledTaskPrincipal -UserId $konto -LogonType Interactive -RunLevel Limited
$einstellungen = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Aufgabe -Action $aktion -Trigger $ausloeser -Principal $prinzipal -Settings $einstellungen `
  -Description "WAMO: Host und SSH-Rücktunnel zu Sokrates-2 (scripts\wamo-tunnel.ps1). Entfernen: scripts\wamo-tunnel-entfernen.ps1" `
  -Force | Out-Null
Write-Host "  eingerichtet"
if ($TokenNeu -and (Get-ScheduledTask -TaskName $Aufgabe).State -eq "Running") {
  # Der laufende Host liest das Token nur beim Start: über die Stop-Datei sauber beenden und neu starten
  New-Item -ItemType File -Force -Path (Join-Path $Daten "tunnel.stop") | Out-Null
  for ($i = 0; $i -lt 30 -and (Get-ScheduledTask -TaskName $Aufgabe).State -eq "Running"; $i++) { Start-Sleep -Seconds 1 }
  $Starten = $true
  Write-Host "  Überwacher wegen neuem Token beendet; Token jetzt an den Kern: python scripts\wamo_kern_sokrates2.py token --neustart"
}
if ($Starten) {
  Start-ScheduledTask -TaskName $Aufgabe
  Write-Host "  gestartet (Log: $Daten\logs\tunnel.log)"
}

Write-Host ""
Write-Host "Weiter:"
Write-Host "  1. Öffentlichen Schlüssel nach Sokrates-2: scp `"$Schluessel.pub`" sokrates2:wamo-infra/wamo-tunnel.pub"
Write-Host "     dann: ssh sokrates2 `"sudo bash ~/wamo-infra/wamo-tunnel-einrichten.sh`""
Write-Host "  2. Token an den Kern (nur über stdin): python scripts\wamo_kern_sokrates2.py token"
