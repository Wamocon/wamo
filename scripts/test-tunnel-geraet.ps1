# Test des Überwachers scripts\wamo-tunnel.ps1 (Gerätebetrieb), isoliert: Kopie in einem nur für diesen Benutzer lesbaren
# Ordner unter %TEMP%, eigener Datenordner, anderer Host-Port, ein Python-Skript statt ssh. Berührt weder die Aufgabe
# "WAMO Tunnel" noch Port 8765 noch %LOCALAPPDATA%\WAMO. Aufruf:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-tunnel-geraet.ps1
# Voraussetzung: host\.venv (cd host; uv sync). Dauer etwa 2 Minuten.
param([string]$Repo = (Split-Path -Parent $PSScriptRoot), [string]$Scratch = $env:TEMP)
$ErrorActionPreference = "Stop"
$T = Join-Path $Scratch "ueb-test"
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
Set-Content -Path (Join-Path $ssh "id_ed25519_wamo_pc") -Value "dummy" -Encoding ascii
Set-Content -Path (Join-Path $ssh "known_hosts_wamo_pc") -Value "dummy" -Encoding ascii
$geraet = @{ v = 1; kennung = "pc-test"; slot = 5; besitzer = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"; hostname = "TEST"; name = "Test"; stufe = "mitglied";
  ziel = "wamo-t-05@192.168.178.136"; socket = "/run/wamo-pc/t05/pc.sock"; fingerabdruck = "SHA256:" + ("A" * 43) }
[System.IO.File]::WriteAllText((Join-Path $daten "geraet.json"), ($geraet | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
$fake = Join-Path $T "fake_ssh.py"
Set-Content -Path $fake -Encoding ascii -Value @'
import sys, time, os
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ssh-empfangen.txt")
args = sys.argv[1:]
roh = b""
while roh.count(b"\n") < 2:
    stueck = sys.stdin.buffer.read1(4096) if hasattr(sys.stdin.buffer, "read1") else sys.stdin.buffer.read(1)
    if not stueck:
        break
    roh += stueck
zeilen = roh.split(b"\n")
with open(out, "w") as f:
    f.write("args=" + " ".join(a for a in args) + "\n")
    f.write("laengen=" + ",".join(str(len(z)) for z in zeilen) + "\n")
    f.write("cr=" + str(roh.count(b"\r")) + " bom=" + str(roh.startswith(b"\xef\xbb\xbf")) + " lf=" + str(roh.count(b"\n")) + "\n")
    f.write("verschieden=" + str(zeilen[0] != zeilen[1]) + "\n")
# wie der echte Zwangsbefehl: warten, bis stdin endet
while sys.stdin.buffer.read(4096):
    pass
'@
$py = Join-Path $R "host\.venv\Scripts\python.exe"
$skript = Join-Path $R "scripts\wamo-tunnel.ps1"
$port = 18765

function Start-Ueb {
  $argumente = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $skript, "-HostPort", $port,
    "-DatenOrdner", $daten, "-SshOrdner", $ssh, "-SshProgramm", $py, "-SshVorab", $fake)
  return Start-Process -FilePath "powershell.exe" -ArgumentList $argumente -PassThru -WindowStyle Hidden
}

$ergebnis = @()
function Pruefe([bool]$ok, [string]$text) { $script:ergebnis += [pscustomobject]@{ ok = $ok; text = $text }; Write-Host (("  ok     " , "  FEHLER ")[-not $ok] + $text) }

Write-Host "== Erster Start"
$ue = Start-Ueb
$statusDatei = Join-Path $daten "tunnel.status"
$ende = (Get-Date).AddSeconds(60)
$status = $null
while ((Get-Date) -lt $ende) {
  Start-Sleep -Seconds 2
  if (Test-Path $statusDatei) {
    try { $status = Get-Content -Raw $statusDatei | ConvertFrom-Json } catch { $status = $null }
    if ($status -and $status.host -and $status.tunnel) { break }
  }
}
$log = Join-Path $daten "logs\tunnel.log"
$logText = if (Test-Path $log) { Get-Content -Raw -Encoding UTF8 $log } else { "" }
Write-Host "--- tunnel.log ---"; Write-Host $logText; Write-Host "------------------"
Pruefe ([bool]($status -and $status.host)) "Host läuft (Herzschlag host=true)"
Pruefe ([bool]($status -and $status.tunnel)) "Tunnel gilt als oben (Herzschlag tunnel=true)"
Pruefe ($status -and $status.modus -eq "geraet") "Modus geraet"
Pruefe ($logText -notmatch "fremdem Prozess") "Kein 'fremder Prozess' beim ersten Start"
Pruefe ($logText -match "Host läuft") "Host läuft im Log"
$tokenDatei = Join-Path $daten "host-token"; $freigabeDatei = Join-Path $daten "host-freigabe"
Pruefe ((Test-Path $tokenDatei) -and (Get-Content -Raw $tokenDatei).Trim().Length -eq 43) "Token-Datei mit 43 Zeichen"
Pruefe ((Test-Path $freigabeDatei) -and (Get-Content -Raw $freigabeDatei).Trim().Length -eq 43) "Freigabe-Datei mit 43 Zeichen"
$tok1 = (Get-Content -Raw $tokenDatei).Trim()
$empf = Join-Path $T "ssh-empfangen.txt"
Pruefe (Test-Path $empf) "ssh hat die Geheimnisse über stdin bekommen"
if (Test-Path $empf) {
  $e = Get-Content -Raw $empf
  Write-Host ($e -replace "(?m)^args=.*$", "args=<verkürzt>")
  Pruefe ($e -match "laengen=43,43,0") "Zwei Zeilen zu 43 Zeichen, danach leer (LF)"
  Pruefe ($e -match "cr=0 bom=False lf=2") "Kein CR, kein BOM"
  Pruefe ($e -match "verschieden=True") "Token und Freigabe-Schlüssel verschieden"
  $args_ = ((Get-Content $empf | Select-Object -First 1))
  Pruefe ($args_ -match "-R /run/wamo-pc/t05/pc.sock:127.0.0.1:$port wamo-t-05@192.168.178.136") "ssh-Ziel und Socket aus geraet.json"
  Pruefe ($args_ -notmatch $tok1) "Token steht nicht in der Befehlszeile"
  Pruefe ($args_ -match "^args=-T " -and $args_ -notmatch " -N ") "ssh mit -T, ohne -N"
}
Pruefe ((Get-Content -Raw -Encoding UTF8 $log) -notmatch [regex]::Escape($tok1)) "Token steht nicht im Log"

Write-Host "== Host-Anfragen (Token, Header)"
$basis = "http://127.0.0.1:$port/mcp"
$body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
function Anfrage($kopf) {
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $basis -Body $body -ContentType "application/json" -Headers $kopf -TimeoutSec 10
    return [int]$r.StatusCode
  } catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } else { return -1 } }
}
$gut = @{ Authorization = "Bearer $tok1"; "X-WAMO-Geraet" = "pc-test"; "X-WAMO-Profil" = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"; Accept = "application/json, text/event-stream" }
Pruefe ((Anfrage $gut) -eq 200) "Anfrage mit Token und passenden Headern: 200"
$o = $gut.Clone(); $o.Remove("X-WAMO-Geraet")
Pruefe ((Anfrage $o) -eq 403) "ohne X-WAMO-Geraet: 403"
$o = $gut.Clone(); $o["X-WAMO-Profil"] = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e"
Pruefe ((Anfrage $o) -eq 403) "fremdes Profil: 403"
$o = $gut.Clone(); $o["Authorization"] = "Bearer " + ("x" * 43)
Pruefe ((Anfrage $o) -eq 401) "falsches Token: 401"

Write-Host "== Prozesse im Job"
$lausch = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
Pruefe ([bool]$lausch) "Host lauscht auf $port"

Write-Host "== Stopp und Neustart des Hosts"
New-Item -ItemType File -Force -Path (Join-Path $daten "tunnel.stop") | Out-Null
$ende = (Get-Date).AddSeconds(30)
while (-not $ue.HasExited -and (Get-Date) -lt $ende) { Start-Sleep -Milliseconds 500 }
Pruefe $ue.HasExited "Überwacher endet nach der Stop-Datei"
Start-Sleep -Seconds 2
Pruefe (-not (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) "Host ist mit dem Überwacher beendet"
$st = Get-Content -Raw $statusDatei | ConvertFrom-Json
Pruefe ($st.ende -eq $true) "Herzschlag meldet ende=true"
Remove-Item -Force -ErrorAction SilentlyContinue $empf

Write-Host "== Zweiter Start: neue Geheimnisse, Hinweis auf unbemerkten Abbruch"
# Herzschlag ohne ende simulieren (wie nach 0xC000013A)
$st.ende = $false
$st.ts = (Get-Date).AddMinutes(-3).ToUniversalTime().ToString("o")
[System.IO.File]::WriteAllText($statusDatei, ($st | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
$ue = Start-Ueb
$ende = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $ende) {
  Start-Sleep -Seconds 2
  try { $status = Get-Content -Raw $statusDatei | ConvertFrom-Json } catch { $status = $null }
  if ($status -and $status.host -and $status.tunnel -and -not $status.ende) { break }
}
$logText = Get-Content -Raw -Encoding UTF8 $log
$tok2 = (Get-Content -Raw $tokenDatei).Trim()
Pruefe ($tok1 -ne $tok2) "Bei jedem Host-Start ein neues Token"
Pruefe ($logText -match "endete unbemerkt") "Log vermerkt den unbemerkten Abbruch des vorigen Laufs"
Pruefe (Test-Path $empf) "ssh bekommt auch beim zweiten Start die Geheimnisse"

Write-Host "== Zweiter Überwacher gleichzeitig"
$zweiter = Start-Ueb
Start-Sleep -Seconds 8
Pruefe $zweiter.HasExited "Zweiter Überwacher beendet sich (Mutex)"
Pruefe ((Get-Content -Raw -Encoding UTF8 $log) -match "Läuft schon") "Log: Läuft schon"

Write-Host "== Fremder Prozess auf dem Port"
New-Item -ItemType File -Force -Path (Join-Path $daten "tunnel.stop") | Out-Null
$ende = (Get-Date).AddSeconds(30)
while (-not $ue.HasExited -and (Get-Date) -lt $ende) { Start-Sleep -Milliseconds 500 }
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse("127.0.0.1"), $port)
$listener.Start()
$ue = Start-Ueb
Start-Sleep -Seconds 12
$logText = Get-Content -Raw -Encoding UTF8 $log
Pruefe ($logText -match "von einem fremden Prozess .* belegt") "Fremder Port-Besitzer wird erkannt"
Pruefe (-not (Test-Path $empf) -or ((Get-Item $empf).LastWriteTime -lt (Get-Date).AddSeconds(-11))) "Kein Tunnel, solange ein Fremder den Port hält"
New-Item -ItemType File -Force -Path (Join-Path $daten "tunnel.stop") | Out-Null
$ende = (Get-Date).AddSeconds(30)
while (-not $ue.HasExited -and (Get-Date) -lt $ende) { Start-Sleep -Milliseconds 500 }
$listener.Stop()
Pruefe $ue.HasExited "Überwacher endet"

$fehler = @($ergebnis | Where-Object { -not $_.ok })
Write-Host ""
Write-Host ("{0} von {1} Prüfungen bestanden." -f ($ergebnis.Count - $fehler.Count), $ergebnis.Count)
$fehler | ForEach-Object { Write-Host ("  FEHLER: " + $_.text) }
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$T*" } | ForEach-Object { Write-Host ("Übrig: python PID " + $_.Id) }
