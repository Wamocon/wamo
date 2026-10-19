# WAMO-Tunnel (Betriebsart Büro): hält auf diesem PC den Host und den SSH-Rücktunnel zu Sokrates-2 am Laufen.
#
#   Host    host\wamo_host.py --http auf 127.0.0.1:8765, Bearer-Token aus %LOCALAPPDATA%\WAMO\host-token
#   Tunnel  ssh -N -R /run/wamo-tunnel/pc.sock:127.0.0.1:8765 wamo-tunnel@192.168.178.136
#
# Gestartet von der geplanten Aufgabe "WAMO Tunnel" bei der Anmeldung (scripts\wamo-tunnel-einrichten.ps1), nur für
# den aktuellen Benutzer, ohne Administratorrechte. Beenden: Datei %LOCALAPPDATA%\WAMO\tunnel.stop anlegen oder
# scripts\wamo-tunnel-entfernen.ps1.
#
# Regeln, die hier gelten:
#   * Beide Kindprozesse laufen in einem Job-Objekt mit KILL_ON_JOB_CLOSE: endet dieser Überwacher (auch hart), enden
#     Host und ssh mit. Es werden nie andere Prozesse beendet.
#   * Der Tunnel läuft nur, solange der eigene Host auf 127.0.0.1:8765 lauscht (Prozess im eigenen Job). Belegt ein
#     fremdes Programm den Port, startet kein Tunnel: sonst bekäme dieses Programm Anfragen und Token des Kerns.
#   * Stirbt der Host, wird der eigene ssh sofort beendet; der Kern meldet dann "PC nicht erreichbar".
#   * Wiederverbindung mit wachsender Pause (2 s bis 60 s). ssh: ExitOnForwardFailure, ServerAlive 15 s x 3,
#     fester Schlüssel, eigene known_hosts mit StrictHostKeyChecking, keine Konfigurationsdatei (-F none).
#   * Der Host bekommt eine knappe Umgebung (kein Erbe von Variablen mit Zugangsdaten), das Token nur als Dateipfad.
#   * Keine Geheimnisse in Befehlszeilen oder Logs.
param(
  [string]$Ziel = "wamo-tunnel@192.168.178.136",
  [string]$Socket = "/run/wamo-tunnel/pc.sock",
  [int]$HostPort = 8765
)

Set-StrictMode -Version 3
$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$HostDir = Join-Path $Repo "host"
$HostPython = Join-Path $HostDir ".venv\Scripts\python.exe"
$Daten = Join-Path $env:LOCALAPPDATA "WAMO"
$TokenDatei = Join-Path $Daten "host-token"
$StopDatei = Join-Path $Daten "tunnel.stop"
$LogDir = Join-Path $Daten "logs"
$Log = Join-Path $LogDir "tunnel.log"
$SshLog = Join-Path $LogDir "ssh.log"
$HostLog = Join-Path $LogDir "host.log"
$Schluessel = Join-Path $env:USERPROFILE ".ssh\id_ed25519_wamo_tunnel"
$KnownHosts = Join-Path $env:USERPROFILE ".ssh\known_hosts_wamo_tunnel"
$Ssh = Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"
$LogMaxBytes = 1MB

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
public static class WamoJob {
  [StructLayout(LayoutKind.Sequential)] struct Basic { public long A; public long B; public uint LimitFlags; public UIntPtr C; public UIntPtr D; public uint E; public UIntPtr F; public uint G; public uint H; }
  [StructLayout(LayoutKind.Sequential)] struct Io { public ulong A, B, C, D, E, F; }
  [StructLayout(LayoutKind.Sequential)] struct Ext { public Basic B; public Io I; public UIntPtr P; public UIntPtr J; public UIntPtr PP; public UIntPtr PJ; }
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr a, string n);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int cls, IntPtr info, uint len);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr proc);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool IsProcessInJob(IntPtr proc, IntPtr job, out bool result);
  [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateJobObject(IntPtr job, uint code);
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

foreach ($datei in @($HostPython, $TokenDatei, $Schluessel, $KnownHosts, $Ssh)) {
  if (-not (Test-Path $datei)) { Schreibe "Fehlt: $datei (scripts\wamo-tunnel-einrichten.ps1 ausführen)"; exit 1 }
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if (-not (Pruefe-NurBenutzer $TokenDatei) -or -not (Pruefe-NurBenutzer $Daten)) {
  Schreibe "Abbruch: $Daten oder das Token ist für andere Konten lesbar (wamo-tunnel-einrichten.ps1 erneut ausführen)"
  exit 1
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
$Code = @($PSCommandPath, $PSScriptRoot, $HostDir, (Join-Path $HostDir "wamo_host.py"), (Join-Path $HostDir "host_http.py"),
  (Join-Path $HostDir "windows_control.py"), (Split-Path -Parent $HostPython), $HostPython)
foreach ($pfad in $Code) {
  $fremd = Fremder-Schreiber $pfad
  if ($fremd) {
    Schreibe "Abbruch: $pfad ist für $fremd beschreibbar (scripts\wamo-tunnel-einrichten.ps1 härtet die Rechte von scripts und host)"
    exit 1
  }
}

# Nur ein Überwacher je Benutzer, auch über Windows-Sitzungen hinweg (RDP, SSH, zweite Anmeldung)
$mutexName = "Global\WAMO-Tunnel-" + [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
if (-not $mutex.WaitOne(0)) { Schreibe "Läuft schon, zweiter Start beendet sich"; exit 0 }

Remove-Item -Force -ErrorAction SilentlyContinue $StopDatei
Kuerze $SshLog
Kuerze $HostLog
Schreibe "Überwacher gestartet (Ziel $Ziel, Socket $Socket, Host-Port $HostPort)"

# Jedes Kind bekommt ein eigenes Job-Objekt: Beende-Kind beendet es samt Unterprozessen (der Python-Starter der
# venv startet den eigentlichen Interpreter als Kind), und endet der Überwacher, schließt Windows die Jobs.
function Starte([string]$Programm, [string[]]$Argumente, [string]$Ordner, [hashtable]$Umgebung) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $Programm
  # Argumente einzeln quoten; keine davon stammt aus dem Netz oder dem Sprachmodell
  $psi.Arguments = ($Argumente | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join " "
  $psi.WorkingDirectory = $Ordner
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  if ($Umgebung) {
    $psi.EnvironmentVariables.Clear()
    foreach ($k in $Umgebung.Keys) { if ($Umgebung[$k]) { $psi.EnvironmentVariables[$k] = [string]$Umgebung[$k] } }
  }
  $job = [WamoJob]::Neu()
  $p = [System.Diagnostics.Process]::Start($psi)
  try { [WamoJob]::Zuordnen($job, $p.Handle) } catch { $p.Kill(); [WamoJob]::Beende($job); throw }
  return [pscustomobject]@{ P = $p; Job = $job }
}

function Beende-Kind($Kind) {
  if ($Kind) { try { [WamoJob]::Beende($Kind.Job) } catch { } }
}

function Laeuft($Kind) { return [bool]($Kind -and -not $Kind.P.HasExited) }

function Lauscher([int]$Port) {
  # PID des Prozesses, der auf 127.0.0.1:<Port> lauscht, oder $null
  $c = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($c) { return [int]$c.OwningProcess } else { return $null }
}

function Starte-Host {
  $fremd = Lauscher $HostPort
  if ($fremd) {
    Schreibe "Port $HostPort ist von einem fremden Prozess (PID $fremd) belegt: kein Host, kein Tunnel"
    return $null
  }
  $umgebung = @{
    SystemRoot = $env:SystemRoot; windir = $env:windir; PATH = $env:PATH; PATHEXT = $env:PATHEXT
    USERPROFILE = $env:USERPROFILE; USERNAME = $env:USERNAME; USERDOMAIN = $env:USERDOMAIN; COMPUTERNAME = $env:COMPUTERNAME
    HOMEDRIVE = $env:HOMEDRIVE; HOMEPATH = $env:HOMEPATH; LOCALAPPDATA = $env:LOCALAPPDATA; APPDATA = $env:APPDATA
    TEMP = $env:TEMP; TMP = $env:TMP; ProgramFiles = $env:ProgramFiles; "ProgramFiles(x86)" = ${env:ProgramFiles(x86)}
    ProgramData = $env:ProgramData; PUBLIC = $env:PUBLIC; SystemDrive = $env:SystemDrive; ComSpec = $env:ComSpec
    PSModulePath = $env:PSModulePath; PYTHONUTF8 = "1"; PYTHONIOENCODING = "utf-8"
    WAMO_HOST_TOKEN_FILE = $TokenDatei; WAMO_HOST_PORT = "$HostPort"; WAMO_HOST_BIND = "127.0.0.1"; WAMO_HOST_LOG = $HostLog
  }
  $kind = Starte $HostPython @("wamo_host.py", "--http") $HostDir $umgebung
  # warten, bis der eigene Host lauscht (höchstens 30 s)
  for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    if ($kind.P.HasExited) {
      Schreibe "Host beendet sich beim Start (Code $($kind.P.ExitCode)), siehe $HostLog"
      Beende-Kind $kind
      return $null
    }
    $lauscher = Lauscher $HostPort
    if ($lauscher -and [WamoJob]::GehoertZu($kind.Job, $lauscher)) { Schreibe "Host läuft (PID $($kind.P.Id))"; return $kind }
    if ($lauscher) { Schreibe "Port $HostPort von fremdem Prozess $lauscher belegt"; Beende-Kind $kind; return $null }
  }
  Schreibe "Host lauscht nach 30 s nicht, wird neu gestartet"
  Beende-Kind $kind
  return $null
}

function Host-Gesund($Kind) {
  # Der Host lebt und lauscht selbst auf dem Port (kein anderer hat ihn übernommen)
  if (-not (Laeuft $Kind)) { return $false }
  $lauscher = Lauscher $HostPort
  return [bool]($lauscher -and [WamoJob]::GehoertZu($Kind.Job, $lauscher))
}

function Starte-Ssh {
  $argumente = @(
    "-N", "-F", "none", "-i", $Schluessel,
    "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no",
    # ssh trennt UserKnownHostsFile an Leerzeichen (mehrere Dateien): "~" löst ssh selbst zum Benutzerordner auf
    "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=~/.ssh/known_hosts_wamo_tunnel",
    "-o", "HostKeyAlgorithms=ssh-ed25519",
    "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
    "-o", "ConnectTimeout=10", "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "ClearAllForwardings=no",
    "-o", "RequestTTY=no", "-o", "ControlMaster=no", "-o", "LogLevel=INFO", "-E", $SshLog,
    "-R", "${Socket}:127.0.0.1:$HostPort", $Ziel
  )
  return Starte $Ssh $argumente $env:USERPROFILE $null
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
      Schreibe ("Tunnel beendet (Code {0}, lief {1:N0} s), siehe {2}" -f $sshKind.P.ExitCode, $dauer, $SshLog)
      Beende-Kind $sshKind
      $sshKind = $null
      if ($dauer -gt 60) { $pause = 2 } else { $pause = [Math]::Min(60, $pause * 2) }
      $naechsterSsh = (Get-Date).AddSeconds($pause)
    }
    if ($hostKind -and -not $sshKind -and (Get-Date) -ge $naechsterSsh) {
      $sshKind = Starte-Ssh
      $sshSeit = Get-Date
      Schreibe "Tunnel wird aufgebaut (PID $($sshKind.P.Id))"
    }
    Start-Sleep -Seconds 2
  }
} finally {
  Beende-Kind $sshKind
  Beende-Kind $hostKind
  Remove-Item -Force -ErrorAction SilentlyContinue $StopDatei
  Schreibe "Überwacher beendet"
  $mutex.ReleaseMutex()
}
