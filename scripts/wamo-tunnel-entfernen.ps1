# Rückweg zu scripts\wamo-tunnel-einrichten.ps1: geplante Aufgabe weg, Überwacher sauber beenden.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wamo-tunnel-entfernen.ps1 [-Alles]
#
# Der Überwacher wird nur über seine Stop-Datei beendet; er beendet dann selbst seinen Host und seinen ssh (beide in
# seinen Job-Objekten). Andere Prozesse fasst dieses Skript nicht an.
# -Alles löscht zusätzlich %LOCALAPPDATA%\WAMO (Token, Logs), den Tunnel-Schlüssel und known_hosts_wamo_tunnel.
param([switch]$Alles)

Set-StrictMode -Version 3
$ErrorActionPreference = "Stop"

$Daten = Join-Path $env:LOCALAPPDATA "WAMO"
$StopDatei = Join-Path $Daten "tunnel.stop"
$Aufgabe = "WAMO Tunnel"

function LaeuftUeberwacher {
  # derselbe sitzungsübergreifende Name wie in wamo-tunnel.ps1
  $m = New-Object System.Threading.Mutex($false, "Global\WAMO-Tunnel-" + [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
  try {
    if ($m.WaitOne(0)) { $m.ReleaseMutex(); return $false }
    return $true
  } catch [System.Threading.AbandonedMutexException] {
    return $false
  } finally { $m.Dispose() }
}

# Stop-Datei immer anlegen (auch wenn der Überwacher in einer anderen Sitzung läuft) und warten
if (Test-Path $Daten) { New-Item -ItemType File -Force -Path $StopDatei | Out-Null }
for ($i = 0; $i -lt 30 -and (LaeuftUeberwacher); $i++) { Start-Sleep -Seconds 1 }
$t = Get-ScheduledTask -TaskName $Aufgabe -ErrorAction SilentlyContinue
if ($t -and $t.State -eq "Running") {
  # Läuft die Aufgabe noch: beenden. Windows schließt dabei die Job-Objekte, Host und ssh enden mit.
  Stop-ScheduledTask -TaskName $Aufgabe
}
if (LaeuftUeberwacher) { Write-Warning "Überwacher läuft nach 30 s noch; die Aufgabe wird trotzdem entfernt." }
else { Write-Host "Überwacher beendet (oder lief nicht)" }
Remove-Item -Force -ErrorAction SilentlyContinue $StopDatei

$t = Get-ScheduledTask -TaskName $Aufgabe -ErrorAction SilentlyContinue
if ($t) {
  Unregister-ScheduledTask -TaskName $Aufgabe -Confirm:$false
  Write-Host "Geplante Aufgabe '$Aufgabe' entfernt"
} else {
  Write-Host "Geplante Aufgabe '$Aufgabe' gibt es nicht"
}

if ($Alles) {
  $ssh = Join-Path $env:USERPROFILE ".ssh"
  foreach ($d in @("$ssh\id_ed25519_wamo_tunnel", "$ssh\id_ed25519_wamo_tunnel.pub", "$ssh\known_hosts_wamo_tunnel")) {
    if (Test-Path $d) { Remove-Item -Force $d; Write-Host "gelöscht: $d" }
  }
  if (Test-Path $Daten) { Remove-Item -Recurse -Force $Daten; Write-Host "gelöscht: $Daten (Token, Logs)" }
  Write-Host ""
  Write-Host "Auf Sokrates-2 außerdem: ssh sokrates2 `"sudo bash ~/wamo-infra/wamo-tunnel-rueckweg.sh`""
  Write-Host "und das Token im Kern löschen: ssh sokrates2 `"sudo /usr/local/sbin/wamo-core-env loeschen WAMO_PC_HOST_TOKEN`""
}
