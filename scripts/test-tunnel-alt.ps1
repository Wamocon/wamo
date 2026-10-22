# Test des Überwachers scripts\wamo-tunnel.ps1 (Altbetrieb und Warten ohne Einrichtung), isoliert: Kopie in einem nur für diesen Benutzer lesbaren
# Ordner unter %TEMP%, eigener Datenordner, anderer Host-Port, ein Python-Skript statt ssh. Berührt weder die Aufgabe
# "WAMO Tunnel" noch Port 8765 noch %LOCALAPPDATA%\WAMO. Aufruf:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-tunnel-alt.ps1
# Voraussetzung: host\.venv (cd host; uv sync). Dauer etwa 2 Minuten.
param([string]$Repo = (Split-Path -Parent $PSScriptRoot), [string]$Scratch = $env:TEMP)
$ErrorActionPreference = "Stop"
$T = Join-Path $Scratch "ueb-test-alt"
if (Test-Path $T) { Remove-Item -Recurse -Force $T }
New-Item -ItemType Directory -Force -Path $T | Out-Null
$ich = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls $T /inheritance:r /grant:r "*${ich}:(OI)(CI)F" | Out-Null
$R = Join-Path $T "repo"
New-Item -ItemType Directory -Force -Path (Join-Path $R "scripts") | Out-Null
Copy-Item (Join-Path $Repo "scripts\wamo-tunnel.ps1") (Join-Path $R "scripts\")
Copy-Item -Recurse (Join-Path $Repo "host") (Join-Path $R "host")
$daten = Join-Path $T "daten"; $ssh = Join-Path $T "ssh"
New-Item -ItemType Directory -Force -Path $daten, $ssh | Out-Null
$py = Join-Path $R "host\.venv\Scripts\python.exe"
$skript = Join-Path $R "scripts\wamo-tunnel.ps1"
$fake = Join-Path $T "fake_ssh.py"
Set-Content -Path $fake -Encoding ascii -Value @'
import sys, time, os
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ssh-args.txt")
open(out, "w").write(" ".join(sys.argv[1:]))
time.sleep(600)
'@
$port = 18766
function Start-Ueb {
  $a = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $skript, "-HostPort", $port, "-DatenOrdner", $daten,
    "-SshOrdner", $ssh, "-SshProgramm", $py, "-SshVorab", $fake)
  return Start-Process -FilePath "powershell.exe" -ArgumentList $a -PassThru -WindowStyle Hidden
}
$ergebnis = @()
function Pruefe([bool]$ok, [string]$text) { $script:ergebnis += [pscustomobject]@{ ok = $ok; text = $text }; Write-Host (("  ok     ", "  FEHLER ")[-not $ok] + $text) }

Write-Host "== Ohne Einrichtung: wartet"
$ue = Start-Ueb
Start-Sleep -Seconds 8
$log = Join-Path $daten "logs\tunnel.log"
$logtext = Get-Content -Raw -Encoding UTF8 $log
Pruefe (-not $ue.HasExited) "Überwacher wartet, endet nicht mit Fehler"
Pruefe ($logtext -match "Nicht eingerichtet") "Log: Nicht eingerichtet"
Write-Host "== Altbetrieb-Dateien erscheinen"
[System.IO.File]::WriteAllText((Join-Path $daten "host-token"), ("T" * 43), (New-Object System.Text.ASCIIEncoding))
Set-Content -Path (Join-Path $ssh "id_ed25519_wamo_tunnel") -Value "dummy" -Encoding ascii
Set-Content -Path (Join-Path $ssh "known_hosts_wamo_tunnel") -Value "dummy" -Encoding ascii
$ende = (Get-Date).AddSeconds(60)
$st = $null
while ((Get-Date) -lt $ende) {
  Start-Sleep -Seconds 2
  try { $st = Get-Content -Raw (Join-Path $daten "tunnel.status") | ConvertFrom-Json } catch { $st = $null }
  if ($st -and $st.host -and $st.tunnel) { break }
}
Pruefe ([bool]($st -and $st.host -and $st.tunnel -and $st.modus -eq "alt")) "Altbetrieb läuft (Host und Tunnel, Modus alt)"
$a = Get-Content -Raw (Join-Path $T "ssh-args.txt")
Pruefe ($a -match "^-N " -and $a -match "-R /run/wamo-tunnel/pc.sock:127.0.0.1:$port wamo-tunnel@192.168.178.136") "ssh -N auf den alten Socket und Benutzer"
Pruefe ((Get-Content -Raw (Join-Path $daten "host-token")) -eq ("T" * 43)) "Token bleibt im Altbetrieb unverändert"
New-Item -ItemType File -Force -Path (Join-Path $daten "tunnel.stop") | Out-Null
$ende = (Get-Date).AddSeconds(30)
while (-not $ue.HasExited -and (Get-Date) -lt $ende) { Start-Sleep -Milliseconds 500 }
Pruefe $ue.HasExited "Überwacher endet nach der Stop-Datei"
Start-Sleep -Seconds 2
Pruefe (-not (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) "Host mit beendet"
$fehler = @($ergebnis | Where-Object { -not $_.ok })
Write-Host ("{0} von {1} Prüfungen bestanden." -f ($ergebnis.Count - $fehler.Count), $ergebnis.Count)

