# WAMO-Tunnel (Betriebsart Büro): hält auf diesem PC den Host und den SSH-Rücktunnel zu Sokrates-2 am Laufen.
#
#   Host    host\wamo_host.py --http auf 127.0.0.1:8765, Bearer-Token aus %LOCALAPPDATA%\WAMO\host-token,
#           Freigabe-Schlüssel (zweites Geheimnis für PC-Freigaben) aus %LOCALAPPDATA%\WAMO\host-freigabe, falls da
#   Tunnel  Gerätebetrieb (geraet.json vorhanden, von der Desktop-App nach der Bindung geschrieben):
#             ssh -T -R /run/wamo-pc/tNN/pc.sock:127.0.0.1:8765 wamo-t-NN@192.168.178.136
#           Altbetrieb (ohne geraet.json, bis alle Geräte umgestellt sind):
#             ssh -N -R /run/wamo-tunnel/pc.sock:127.0.0.1:8765 wamo-tunnel@192.168.178.136
#
# Gestartet von der geplanten Aufgabe "WAMO Tunnel" bei der Anmeldung und alle 5 Minuten (scripts\wamo-tunnel-einrichten.ps1),
# nur für den aktuellen Benutzer, ohne Administratorrechte. Beenden: Datei %LOCALAPPDATA%\WAMO\tunnel.stop anlegen oder
# scripts\wamo-tunnel-entfernen.ps1.
#
# Gerätebetrieb: Bei JEDEM Start des Hosts erzeugt dieser Überwacher ein neues Host-Token und einen neuen Freigabe-
# Schlüssel (zufällig, 256 Bit), schreibt sie in die beiden Dateien für den Host und schickt sie beim Tunnelaufbau über
# die SSH-Verbindung (stdin, zwei Zeilen mit LF) an den Zwangsbefehl auf Sokrates-2. Der legt sie nur im Ordner dieses
# Geräts ab (tmpfs) und löscht sie beim Trennen. Die Geheimnisse stehen nie in einer Befehlszeile oder im Log.
#
# Regeln, die hier gelten:
#   * Beide Kindprozesse laufen in einem Job-Objekt mit KILL_ON_JOB_CLOSE: endet dieser Überwacher (auch hart), enden
#     Host und ssh mit. Es werden nie andere Prozesse beendet.
#   * Der Host wird ANGEHALTEN gestartet (CREATE_SUSPENDED), dem Job zugeordnet und erst dann fortgesetzt. So gehört auch
#     der lauschende Kindprozess der venv (der Python-Starter startet den eigentlichen Interpreter als Kind) von Anfang
#     an zum Job; sonst meldete der erste Start fälschlich "Port von fremdem Prozess belegt".
#   * Der Tunnel läuft nur, solange der eigene Host auf 127.0.0.1:8765 lauscht (Prozess im eigenen Job). Belegt ein
#     fremdes Programm den Port, startet kein Tunnel: sonst bekäme dieses Programm Anfragen und Token des Kerns.
#   * Stirbt der Host, wird der eigene ssh sofort beendet; der Kern meldet dann "PC nicht erreichbar".
#   * Wiederverbindung mit wachsender Pause (2 s bis 60 s); Exit 75 des Zwangsbefehls (neuere Verbindung desselben
#     Geräts hat übernommen) verbindet nach 1 s neu. ssh: ExitOnForwardFailure, ServerAlive 15 s x 3, fester Schlüssel,
#     eigene known_hosts mit StrictHostKeyChecking, keine Konfigurationsdatei (-F none).
#   * Der Host bekommt eine knappe Umgebung (kein Erbe von Variablen mit Zugangsdaten), das Token nur als Dateipfad.
#   * Herzschlag: alle 10 s schreibt der Überwacher %LOCALAPPDATA%\WAMO\tunnel.status (JSON, ohne Geheimnisse). Die
#     Desktop-App und der nächste Start lesen ihn: endet der Überwacher unbemerkt (zum Beispiel 0xC000013A beim Abmelden
#     oder Schließen der Konsole), startet ihn die Aufgabe spätestens nach 5 Minuten neu, und der nächste Lauf vermerkt
#     das im Log.
#   * Ohne Einrichtung (weder geraet.json noch Altbetrieb-Dateien) wartet der Überwacher auf die Bindung, statt mit
#     Fehler zu enden (sonst startete die Aufgabe ihn im Minutentakt neu).
#   * Keine Geheimnisse in Befehlszeilen oder Logs.
#
# Die Parameter DatenOrdner, SshOrdner, SshProgramm und SshVorab gibt es nur für Tests; die Aufgabe setzt keinen davon.
param(
  [string]$Ziel = "wamo-tunnel@192.168.178.136",
  [string]$Socket = "/run/wamo-tunnel/pc.sock",
  [int]$HostPort = 8765,
  [string]$DatenOrdner = "",
  [string]$SshOrdner = "",
  [string]$SshProgramm = "",
  [string[]]$SshVorab = @()
)

Set-StrictMode -Version 3
$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$HostDir = Join-Path $Repo "host"
$HostPython = Join-Path $HostDir ".venv\Scripts\python.exe"
$Daten = if ($DatenOrdner) { $DatenOrdner } else { Join-Path $env:LOCALAPPDATA "WAMO" }
$SshDir = if ($SshOrdner) { $SshOrdner } else { Join-Path $env:USERPROFILE ".ssh" }
$TokenDatei = Join-Path $Daten "host-token"
$FreigabeDatei = Join-Path $Daten "host-freigabe"
$GeraetDatei = Join-Path $Daten "geraet.json"
$StatusDatei = Join-Path $Daten "tunnel.status"
$StopDatei = Join-Path $Daten "tunnel.stop"
$LogDir = Join-Path $Daten "logs"
$Log = Join-Path $LogDir "tunnel.log"
$SshLog = Join-Path $LogDir "ssh.log"
$HostLog = Join-Path $LogDir "host.log"
$SchluesselAlt = Join-Path $SshDir "id_ed25519_wamo_tunnel"
$KnownHostsAlt = Join-Path $SshDir "known_hosts_wamo_tunnel"
$SchluesselGeraet = Join-Path $SshDir "id_ed25519_wamo_pc"
$KnownHostsGeraet = Join-Path $SshDir "known_hosts_wamo_pc"
$Ssh = if ($SshProgramm) { $SshProgramm } else { Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe" }
$LogMaxBytes = 1MB
$HerzschlagSekunden = 10

function Schreibe([string]$Text) {
  $zeile = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Text
  try {
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt $LogMaxBytes) { Move-Item -Force $Log "$Log.1" }
    Add-Content -Path $Log -Value $zeile -Encoding UTF8
  } catch { }
}

function Kuerze([string]$Datei) {
  try { if ((Test-Path $Datei) -and (Get-Item $Datei).Length -gt $LogMaxBytes) { Move-Item -Force $Datei "$Datei.1" } } catch { }
}

# ---------- Job-Objekt (Kinder enden mit dem Überwacher) ----------
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class WamoJob {
  [StructLayout(LayoutKind.Sequential)] struct Basic { public long A; public long B; public uint LimitFlags; public UIntPtr C; public UIntPtr D; public uint E; public UIntPtr F; public uint G; public uint H; }
  [StructLayout(LayoutKind.Sequential)] struct Io { public ulong A, B, C, D, E, F; }
  [StructLayout(LayoutKind.Sequential)] struct Ext { public Basic B; public Io I; public UIntPtr P; public UIntPtr J; public UIntPtr PP; public UIntPtr PJ; }
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct StartInfo {
    public int cb; public string Reserved; public string Desktop; public string Title;
    public int X, Y, XSize, YSize, XChars, YChars, Fill, Flags; public short ShowWindow, Reserved2;
    public IntPtr Reserved2Ptr, StdIn, StdOut, StdErr;
  }
  [StructLayout(LayoutKind.Sequential)] struct ProcInfo { public IntPtr Process; public IntPtr Thread; public int ProcessId; public int ThreadId; }
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr a, string n);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int cls, IntPtr info, uint len);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr proc);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool IsProcessInJob(IntPtr proc, IntPtr job, out bool result);
  [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateJobObject(IntPtr job, uint code);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateProcess(IntPtr proc, uint code);
  [DllImport("kernel32.dll", SetLastError = true)] static extern uint ResumeThread(IntPtr thread);
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  static extern bool CreateProcessW(string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref StartInfo si, out ProcInfo pi);
  public static void Beende(IntPtr job) { TerminateJobObject(job, 1); CloseHandle(job); }
  public static IntPtr Neu() {
    IntPtr job = CreateJobObject(IntPtr.Zero, null);
    if (job == IntPtr.Zero) throw new InvalidOperationException("CreateJobObject " + Marshal.GetLastWin32Error());
    Ext e = new Ext();
    e.B.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    int n = Marshal.SizeOf(typeof(Ext));
    IntPtr p = Marshal.AllocHGlobal(n);
    try {
      Marshal.StructureToPtr(e, p, false);
      if (!SetInformationJobObject(job, 9, p, (uint)n)) throw new InvalidOperationException("SetInformationJobObject " + Marshal.GetLastWin32Error());
    } finally { Marshal.FreeHGlobal(p); }
    return job;
  }
  public static void Zuordnen(IntPtr job, IntPtr proc) {
    if (!AssignProcessToJobObject(job, proc)) throw new InvalidOperationException("AssignProcessToJobObject " + Marshal.GetLastWin32Error());
  }
  // Prozess ANGEHALTEN erzeugen, dem Job zuordnen, dann fortsetzen: Kinder, die er sofort startet, erben die Job-Zugehörigkeit.
  // envBlock: "NAME=Wert" Einträge, jeder mit einem Nullzeichen beendet (das abschließende zweite Nullzeichen hängt die Funktion an).
  public static int StarteImJob(IntPtr job, string exe, string cmdline, string cwd, string envBlock) {
    StartInfo si = new StartInfo();
    si.cb = Marshal.SizeOf(typeof(StartInfo));
    ProcInfo pi;
    IntPtr env = Marshal.StringToHGlobalUni(envBlock);
    try {
      // 0x4 CREATE_SUSPENDED, 0x400 CREATE_UNICODE_ENVIRONMENT, 0x08000000 CREATE_NO_WINDOW
      if (!CreateProcessW(exe, new StringBuilder(cmdline), IntPtr.Zero, IntPtr.Zero, false, 0x4u | 0x400u | 0x08000000u, env, cwd, ref si, out pi))
        throw new InvalidOperationException("CreateProcess " + Marshal.GetLastWin32Error());
    } finally { Marshal.FreeHGlobal(env); }
    try {
      if (!AssignProcessToJobObject(job, pi.Process)) {
        int fehler = Marshal.GetLastWin32Error();
        TerminateProcess(pi.Process, 1);
        throw new InvalidOperationException("AssignProcessToJobObject " + fehler);
      }
      if (ResumeThread(pi.Thread) == 0xFFFFFFFF) {
        int fehler = Marshal.GetLastWin32Error();
        TerminateProcess(pi.Process, 1);
        throw new InvalidOperationException("ResumeThread " + fehler);
      }
    } finally { CloseHandle(pi.Thread); CloseHandle(pi.Process); }
    return pi.ProcessId;
  }
  public static bool GehoertZu(IntPtr job, int pid) {
    IntPtr h = OpenProcess(0x1000, false, pid); // PROCESS_QUERY_LIMITED_INFORMATION
    if (h == IntPtr.Zero) return false;
    try { bool drin; return IsProcessInJob(h, job, out drin) && drin; } finally { CloseHandle(h); }
  }
}
"@

# ---------- Prüfungen vor dem Start ----------
function Pruefe-NurBenutzer([string]$Pfad) {
  # Token und Ordner dürfen nur für den angemeldeten Benutzer zugänglich sein (eingerichtet von wamo-tunnel-einrichten.ps1)
  $ich = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $acl = Get-Acl -Path $Pfad
  foreach ($regel in $acl.Access) {
    $sid = $regel.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    if ($regel.AccessControlType -eq "Allow" -and $sid -ne $ich) { return $false }
  }
  return $true
}

function Fremder-Schreiber([string]$Pfad) {
  # SID eines Kontos außer diesem Benutzer, SYSTEM und Administratoren, das hier schreiben darf (oder $null).
  # Der Überwacher startet Code aus dem Repo mit Token und Tunnelschlüssel: den darf kein anderes Konto ändern.
  $ok = @([System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value, "S-1-5-18", "S-1-5-32-544")
  # WriteData, AppendData, WriteEA, DeleteChild, WriteAttributes, Delete, WriteDAC, WriteOwner, GENERIC_ALL, GENERIC_WRITE
  $maske = 0x2 -bor 0x4 -bor 0x10 -bor 0x40 -bor 0x100 -bor 0x10000 -bor 0x40000 -bor 0x80000 -bor 0x10000000 -bor 0x40000000
  foreach ($regel in (Get-Acl -Path $Pfad).Access) {
    if ($regel.AccessControlType -ne "Allow") { continue }
    $sid = $regel.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    if ($ok -contains $sid) { continue }
    if (([int64]$regel.FileSystemRights -band $maske) -ne 0) { return $sid }
  }
  return $null
}

# ---------- Betriebsart und Gerätedaten ----------
# Nur strenge Muster: \z statt $ (.NET lässt vor $ ein abschließendes LF zu), IPv4 ohne führende Nullen
$ZielRegex = '\Awamo-t-(0[1-9]|[1-5][0-9]|6[0-4])@((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])(\.(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])){3})\z'
$SocketRegex = '\A/run/wamo-pc/t(0[1-9]|[1-5][0-9]|6[0-4])/pc\.sock\z'
$KennungRegex = '\Apc-[a-z0-9]+(-[a-z0-9]+)*\z'
$BesitzerRegex = '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z'

function Lies-Geraet {
  # geraet.json lesen und streng prüfen (die Datei liegt im Ordner dieses Benutzers, wird aber nie ungeprüft in eine
  # Befehlszeile übernommen). Liefert Ziel, Socket, Kennung, Besitzer oder $null mit Grund im Log.
  try {
    $roh = Get-Content -Raw -Encoding UTF8 -Path $GeraetDatei | ConvertFrom-Json
    $z = [string]$roh.ziel; $s = [string]$roh.socket; $k = [string]$roh.kennung; $b = ([string]$roh.besitzer).ToLower()
  } catch {
    Schreibe "geraet.json nicht lesbar ($($_.Exception.GetType().Name))"
    return $null
  }
  if ($z -cnotmatch $ZielRegex -or $s -cnotmatch $SocketRegex -or $k -cnotmatch $KennungRegex -or $k.Length -gt 23 -or $b -cnotmatch $BesitzerRegex) {
    Schreibe "geraet.json enthält ungültige Werte (Ziel, Socket, Kennung oder Besitzer)"
    return $null
  }
  # Benutzer und Ordner müssen dieselbe Nummer tragen (wamo-t-05 und /run/wamo-pc/t05/)
  if ($z.Substring(7, 2) -ne $s.Substring(14, 2)) {
    Schreibe "geraet.json: Benutzer und Socket gehören zu verschiedenen Plätzen"
    return $null
  }
  return @{ Ziel = $z; Socket = $s; Kennung = $k; Besitzer = $b }
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$GeraetModus = Test-Path $GeraetDatei
$AltDateien = @($TokenDatei, $SchluesselAlt, $KnownHostsAlt)
$AltVorhanden = -not (@($AltDateien | Where-Object { -not (Test-Path $_) }).Count)

# Nur ein Überwacher je Benutzer, auch über Windows-Sitzungen hinweg (RDP, SSH, zweite Anmeldung)
$mutexName = "Global\WAMO-Tunnel-" + [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($DatenOrdner) { $mutexName += "-" + ($DatenOrdner -replace '[^A-Za-z0-9]', '_') }  # Tests: eigener Name
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
if (-not $mutex.WaitOne(0)) { Schreibe "Läuft schon, zweiter Start beendet sich"; exit 0 }

try {
  # Hinweis auf einen unbemerkten Abbruch des vorigen Laufs (Herzschlag ohne "ende")
  if (Test-Path $StatusDatei) {
    try {
      $alt = Get-Content -Raw -Encoding UTF8 -Path $StatusDatei | ConvertFrom-Json
      $alter = ((Get-Date).ToUniversalTime() - [DateTime]::Parse([string]$alt.ts, $null, [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()).TotalSeconds
      if (-not ($alt.PSObject.Properties.Name -contains "ende" -and $alt.ende -eq $true)) {
        Schreibe ("Der vorige Lauf endete unbemerkt (letzter Herzschlag vor {0:N0} s, Modus {1})" -f $alter, $alt.modus)
      }
    } catch { }
  }

  # Ohne Einrichtung warten (die Desktop-App schreibt geraet.json nach der Bindung), statt mit Fehler zu enden
  $gemeldet = $false
  while (-not $GeraetModus -and -not $AltVorhanden) {
    if (Test-Path $StopDatei) { Schreibe "Stop-Datei gefunden, beende (ohne Einrichtung)"; Remove-Item -Force -ErrorAction SilentlyContinue $StopDatei; exit 0 }
    if (-not $gemeldet) { Schreibe "Nicht eingerichtet: warte auf geraet.json (Anmeldung in der Desktop-App) oder die Dateien des Altbetriebs"; $gemeldet = $true }
    Start-Sleep -Seconds 15
    $GeraetModus = Test-Path $GeraetDatei
    $AltVorhanden = -not (@($AltDateien | Where-Object { -not (Test-Path $_) }).Count)
  }
  $Modus = if ($GeraetModus) { "geraet" } else { "alt" }
  $Schluessel = if ($GeraetModus) { $SchluesselGeraet } else { $SchluesselAlt }
  $KnownHosts = if ($GeraetModus) { $KnownHostsGeraet } else { $KnownHostsAlt }

  $vorausgesetzt = @($HostPython, $Schluessel, $KnownHosts, $Ssh)
  if (-not $GeraetModus) { $vorausgesetzt += $TokenDatei }
  foreach ($datei in $vorausgesetzt) {
    if (-not (Test-Path $datei)) { Schreibe "Fehlt: $datei (scripts\wamo-tunnel-einrichten.ps1 ausführen, oder in der Desktop-App anmelden)"; exit 1 }
  }
  if (-not (Pruefe-NurBenutzer $Daten) -or ((Test-Path $TokenDatei) -and -not (Pruefe-NurBenutzer $TokenDatei))) {
    Schreibe "Abbruch: $Daten oder das Token ist für andere Konten lesbar (wamo-tunnel-einrichten.ps1 erneut ausführen)"
    exit 1
  }
  # Freigabe-Schlüssel im Altbetrieb: optional. Fehlt er oder ist er für andere lesbar, startet der Host ohne ihn; dann
  # lässt sich keine kritische PC-Aktion freigeben (alles andere läuft wie bisher). Im Gerätebetrieb erzeugt ihn der Überwacher.
  $FreigabeFuerHost = ""
  if (-not $GeraetModus) {
    if (-not (Test-Path $FreigabeDatei)) {
      Schreibe "Hinweis: $FreigabeDatei fehlt, PC-Freigaben bleiben aus (wamo-tunnel-einrichten.ps1 legt ihn an)"
    } elseif (-not (Pruefe-NurBenutzer $FreigabeDatei)) {
      Schreibe "Warnung: $FreigabeDatei ist für andere Konten lesbar, PC-Freigaben bleiben aus (wamo-tunnel-einrichten.ps1)"
    } else {
      $FreigabeFuerHost = $FreigabeDatei
    }
  }

  # Der Überwacher startet Code aus dem Repo mit Token und Tunnelschlüssel: den darf kein anderes Konto ändern
  $Code = @($PSCommandPath, $PSScriptRoot, $HostDir, (Join-Path $HostDir "wamo_host.py"), (Join-Path $HostDir "host_http.py"),
    (Join-Path $HostDir "windows_control.py"), (Split-Path -Parent $HostPython), $HostPython)
  foreach ($pfad in $Code) {
    $fremd = Fremder-Schreiber $pfad
    if ($fremd) {
      Schreibe "Abbruch: $pfad ist für $fremd beschreibbar (scripts\wamo-tunnel-einrichten.ps1 härtet die Rechte von scripts und host)"
      exit 1
    }
  }

  Remove-Item -Force -ErrorAction SilentlyContinue $StopDatei
  Kuerze $SshLog
  Kuerze $HostLog
  Schreibe "Überwacher gestartet (Modus $Modus, Host-Port $HostPort)"

  # ---------- Geheimnisse (Gerätebetrieb: bei jedem Host-Start neu) ----------
  $script:HostToken = $null
  $script:HostFreigabe = $null

  function Neues-Geheimnis {
    # 256 Bit aus dem Zufallsgenerator des Systems, base64url ohne Auffüllung (43 Zeichen)
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
  }

  function Erneuere-Geheimnisse {
    $token = Neues-Geheimnis
    $freigabe = Neues-Geheimnis
    $ascii = New-Object System.Text.ASCIIEncoding
    [System.IO.File]::WriteAllText($TokenDatei, $token, $ascii)
    [System.IO.File]::WriteAllText($FreigabeDatei, $freigabe, $ascii)
    if (-not (Pruefe-NurBenutzer $TokenDatei) -or -not (Pruefe-NurBenutzer $FreigabeDatei)) {
      throw "Token oder Freigabe-Schlüssel ist für andere Konten lesbar"
    }
    $script:HostToken = $token
    $script:HostFreigabe = $freigabe
  }

  # ---------- Kindprozesse ----------
  # Jedes Kind bekommt ein eigenes Job-Objekt: Beende-Kind beendet es samt Unterprozessen, und endet der Überwacher,
  # schließt Windows die Jobs.
  function Quote-Argument([string]$a) {
    if ($a -match '[\s"]') { return '"' + ($a -replace '"', '\"') + '"' }
    return $a
  }

  function Starte-Host-Angehalten([string]$Programm, [string[]]$Argumente, [string]$Ordner, [System.Collections.IDictionary]$Umgebung) {
    $job = [WamoJob]::Neu()
    try {
      $befehl = ((@($Programm) + $Argumente) | ForEach-Object { Quote-Argument $_ }) -join " "
      $block = New-Object System.Text.StringBuilder
      foreach ($k in $Umgebung.Keys) { if ($Umgebung[$k]) { [void]$block.Append($k).Append("=").Append([string]$Umgebung[$k]).Append([char]0) } }
      $pid_ = [WamoJob]::StarteImJob($job, $Programm, $befehl, $Ordner, $block.ToString())
      $p = [System.Diagnostics.Process]::GetProcessById($pid_)
    } catch {
      [WamoJob]::Beende($job)
      throw
    }
    return [pscustomobject]@{ P = $p; Job = $job }
  }

  function Starte-Ssh-Prozess([string]$Programm, [string[]]$Argumente, [string]$Ordner, [bool]$MitStdin) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Programm
    # Argumente einzeln quoten; keine davon stammt aus dem Netz oder dem Sprachmodell
    $psi.Arguments = ($Argumente | ForEach-Object { Quote-Argument $_ }) -join " "
    $psi.WorkingDirectory = $Ordner
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $MitStdin
    $job = [WamoJob]::Neu()
    try {
      $p = [System.Diagnostics.Process]::Start($psi)
      try { [WamoJob]::Zuordnen($job, $p.Handle) } catch { $p.Kill(); throw }
    } catch {
      [WamoJob]::Beende($job)
      throw
    }
    return [pscustomobject]@{ P = $p; Job = $job }
  }

  function Beende-Kind($Kind) {
    if ($Kind) { try { [WamoJob]::Beende($Kind.Job) } catch { } }
  }

  function Laeuft($Kind) { return [bool]($Kind -and -not $Kind.P.HasExited) }

  function Lauscher-Alle([int]$Port) {
    # PIDs ALLER Prozesse, die auf <Port> lauschen (jede lokale Adresse, IPv4 und IPv6): Ein zweiter Lauscher auf demselben
    # Port (etwa mit SO_REUSEADDR oder auf 0.0.0.0) würde sonst Anfragen und Token mitbekommen.
    return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)
  }

  function Alle-Im-Job($Kind, [int[]]$Pids) {
    # Es lauscht mindestens einer, und jeder davon gehört zum Job des eigenen Hosts
    if (-not $Pids -or $Pids.Count -eq 0) { return $false }
    foreach ($p in $Pids) { if (-not [WamoJob]::GehoertZu($Kind.Job, $p)) { return $false } }
    return $true
  }

  function Starte-Host {
    $fremd = @(Lauscher-Alle $HostPort)
    if ($fremd.Count -gt 0) {
      Schreibe "Port $HostPort ist von einem fremden Prozess (PID $($fremd -join ', ')) belegt: kein Host, kein Tunnel"
      return $null
    }
    if ($GeraetModus) {
      try { Erneuere-Geheimnisse } catch { Schreibe "Geheimnisse nicht erneuert ($($_.Exception.Message))"; return $null }
    }
    $umgebung = [ordered]@{
      SystemRoot = $env:SystemRoot; windir = $env:windir; PATH = $env:PATH; PATHEXT = $env:PATHEXT
      USERPROFILE = $env:USERPROFILE; USERNAME = $env:USERNAME; USERDOMAIN = $env:USERDOMAIN; COMPUTERNAME = $env:COMPUTERNAME
      HOMEDRIVE = $env:HOMEDRIVE; HOMEPATH = $env:HOMEPATH; LOCALAPPDATA = $env:LOCALAPPDATA; APPDATA = $env:APPDATA
      TEMP = $env:TEMP; TMP = $env:TMP; ProgramFiles = $env:ProgramFiles; "ProgramFiles(x86)" = ${env:ProgramFiles(x86)}
      ProgramData = $env:ProgramData; PUBLIC = $env:PUBLIC; SystemDrive = $env:SystemDrive; ComSpec = $env:ComSpec
      PSModulePath = $env:PSModulePath; PYTHONUTF8 = "1"; PYTHONIOENCODING = "utf-8"
      WAMO_HOST_TOKEN_FILE = $TokenDatei; WAMO_HOST_PORT = "$HostPort"; WAMO_HOST_BIND = "127.0.0.1"; WAMO_HOST_LOG = $HostLog
      WAMO_HOST_FREIGABE_DATEI = $(if ($GeraetModus) { $FreigabeDatei } else { $FreigabeFuerHost })  # leer: nicht gesetzt
      WAMO_HOST_GERAET_DATEI = $(if ($GeraetModus) { $GeraetDatei } else { "" })
    }
    try {
      $kind = Starte-Host-Angehalten $HostPython @("wamo_host.py", "--http") $HostDir $umgebung
    } catch {
      Schreibe "Host startet nicht ($($_.Exception.Message))"
      return $null
    }
    # warten, bis der eigene Host lauscht (höchstens 30 s)
    for ($i = 0; $i -lt 60; $i++) {
      Start-Sleep -Milliseconds 500
      if ($kind.P.HasExited) {
        Schreibe "Host beendet sich beim Start (Code $($kind.P.ExitCode)), siehe $HostLog"
        Beende-Kind $kind
        return $null
      }
      $lauscher = @(Lauscher-Alle $HostPort)
      if ($lauscher.Count -gt 0) {
        if (Alle-Im-Job $kind $lauscher) { Schreibe "Host läuft (PID $($kind.P.Id))"; return $kind }
        Schreibe "Port $HostPort von fremdem Prozess $($lauscher -join ', ') belegt"
        Beende-Kind $kind
        return $null
      }
    }
    Schreibe "Host lauscht nach 30 s nicht, wird neu gestartet"
    Beende-Kind $kind
    return $null
  }

  function Host-Gesund($Kind) {
    # Der Host lebt und lauscht selbst auf dem Port (kein anderer hat ihn übernommen)
    if (-not (Laeuft $Kind)) { return $false }
    return [bool](Alle-Im-Job $Kind @(Lauscher-Alle $HostPort))
  }

  function Starte-Ssh {
    $zielAdresse = $Ziel
    $socketPfad = $Socket
    if ($GeraetModus) {
      $g = Lies-Geraet
      if (-not $g) { return $null }
      $zielAdresse = $g.Ziel
      $socketPfad = $g.Socket
    }
    $argumente = @($SshVorab) + @(
      $(if ($GeraetModus) { "-T" } else { "-N" }), "-F", "none", "-i", $Schluessel,
      "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no",
      # ssh trennt UserKnownHostsFile an Leerzeichen (mehrere Dateien): "~" löst ssh selbst zum Benutzerordner auf
      "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=$(if ($GeraetModus) { '~/.ssh/known_hosts_wamo_pc' } else { '~/.ssh/known_hosts_wamo_tunnel' })",
      "-o", "HostKeyAlgorithms=ssh-ed25519",
      "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
      "-o", "ConnectTimeout=10", "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "ClearAllForwardings=no",
      "-o", "RequestTTY=no", "-o", "ControlMaster=no", "-o", "LogLevel=INFO", "-E", $SshLog,
      "-R", "${socketPfad}:127.0.0.1:$HostPort", $zielAdresse
    )
    $kind = Starte-Ssh-Prozess $Ssh $argumente $env:USERPROFILE $GeraetModus
    if ($GeraetModus) {
      # Token und Freigabe-Schlüssel an den Zwangsbefehl: zwei Zeilen mit LF (nicht CRLF), stdin bleibt offen. Über den
      # Byte-Strom, damit keine Kodierung ein BOM voranstellt.
      try {
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($script:HostToken + "`n" + $script:HostFreigabe + "`n")
        $kind.P.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        $kind.P.StandardInput.BaseStream.Flush()
      } catch {
        Schreibe "Geheimnisse konnten nicht an ssh übergeben werden ($($_.Exception.GetType().Name))"
        Beende-Kind $kind
        return $null
      } finally {
        $bytes = $null
      }
    }
    return $kind
  }

  # ---------- Herzschlag ----------
  $script:StatusUm = [DateTime]::MinValue
  function Schreibe-Status($HostKind, $SshKind, $SshSeit, [bool]$Ende = $false) {
    $jetzt = Get-Date
    if (-not $Ende -and ($jetzt - $script:StatusUm).TotalSeconds -lt $HerzschlagSekunden) { return }
    $script:StatusUm = $jetzt
    $tunnelOben = [bool]((Laeuft $SshKind) -and $SshSeit -and (($jetzt - $SshSeit).TotalSeconds -ge 4))
    $status = [ordered]@{
      v = 1; pid = $PID; ts = $jetzt.ToUniversalTime().ToString("o"); host = (Laeuft $HostKind); tunnel = $tunnelOben
      seit = $(if ($tunnelOben) { $SshSeit.ToUniversalTime().ToString("o") } else { $null }); modus = $Modus; ende = $Ende
    }
    try {
      $tmp = "$StatusDatei.neu"
      [System.IO.File]::WriteAllText($tmp, ($status | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
      Move-Item -Force -Path $tmp -Destination $StatusDatei
    } catch { }
  }

  $hostKind = $null
  $sshKind = $null
  $pause = 2
  $naechsterSsh = Get-Date
  $naechsterHost = Get-Date
  $sshSeit = $null

  try {
    while ($true) {
      if (Test-Path $StopDatei) { Schreibe "Stop-Datei gefunden, beende"; break }

      if ($hostKind -and -not (Host-Gesund $hostKind)) {
        $code = if ($hostKind.P.HasExited) { $hostKind.P.ExitCode } else { "lauscht nicht mehr" }
        Schreibe "Host nicht mehr bereit ($code)"
        Beende-Kind $hostKind
        $hostKind = $null
        if (Laeuft $sshKind) { Beende-Kind $sshKind; $sshKind = $null; Schreibe "Tunnel getrennt, weil der Host fehlt" }
        $naechsterHost = (Get-Date).AddSeconds(5)
      }
      if (-not $hostKind -and (Get-Date) -ge $naechsterHost) {
        $hostKind = Starte-Host
        if (-not $hostKind) { $naechsterHost = (Get-Date).AddSeconds(30) }
      }

      if ($sshKind -and $sshKind.P.HasExited) {
        $dauer = if ($sshSeit) { ((Get-Date) - $sshSeit).TotalSeconds } else { 0 }
        $endCode = $sshKind.P.ExitCode
        Schreibe ("Tunnel beendet (Code {0}, lief {1:N0} s), siehe {2}" -f $endCode, $dauer, $SshLog)
        Beende-Kind $sshKind
        $sshKind = $null
        if ($endCode -eq 75) { $pause = 1 }  # eine neuere Verbindung dieses Geräts hat übernommen: sofort neu verbinden
        elseif ($dauer -gt 60) { $pause = 2 } else { $pause = [Math]::Min(60, $pause * 2) }
        $naechsterSsh = (Get-Date).AddSeconds($pause)
      }
      if ($hostKind -and -not $sshKind -and (Get-Date) -ge $naechsterSsh) {
        try {
          $sshKind = Starte-Ssh
        } catch {
          Schreibe "ssh startet nicht ($($_.Exception.Message))"
          $sshKind = $null
        }
        if ($sshKind) {
          $sshSeit = Get-Date
          Schreibe "Tunnel wird aufgebaut (PID $($sshKind.P.Id))"
        } else {
          $naechsterSsh = (Get-Date).AddSeconds(15)
        }
      }
      Schreibe-Status $hostKind $sshKind $sshSeit
      Start-Sleep -Seconds 2
    }
  } finally {
    Beende-Kind $sshKind
    Beende-Kind $hostKind
    Remove-Item -Force -ErrorAction SilentlyContinue $StopDatei
    if ($script:StatusUm -gt [DateTime]::MinValue) { Schreibe-Status $null $null $null $true }
    Schreibe "Überwacher beendet"
  }
} finally {
  try { $mutex.ReleaseMutex() } catch { }
}
