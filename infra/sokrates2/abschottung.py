"""Landlock-Abschottung für Aufträge in der Sandbox auf Sokrates-2 (läuft in bubblewrap vor Claude Code).

Aufruf (durch wamo-job.sh; installiert als /srv/wamo/app/waechter/abschottung.py, in der Sandbox
/opt/wamo/waechter/abschottung.py):

    python3 -I abschottung.py --sperrports 22,5432,11434 -- befehl [argumente ...]
    python3 -I abschottung.py --seccomp-bpf [aarch64|x86_64] > seccomp.bpf     (bei der Installation)

Das Netz bleibt an (Claude-API, GitHub, npm), aber zwei Wege zu Diensten auf dem Host werden zu:

  * TCP-Verbindungen zu den gesperrten Ports. wamo-job.sh übergibt alle Ports, auf denen der Host beim
    Start lauscht (Postgres, Supabase, Ollama, vLLM, ...), plus feste Zusatzports aus wamo-job.conf.
    Landlock kennt nur Ports, keine Adressen: Port 443 bleibt deshalb auch zu Rechnern im LAN offen.
  * Abstrakte Unix-Sockets außerhalb der Sandbox (zum Beispiel @/tmp/.X11-unix/X1 einer Desktop-Sitzung)
    und Signale an Prozesse außerhalb.

Das Binden eigener TCP-Ports ist nur im Bereich 20000-20999 erlaubt (eigene Entwicklungsserver, M3), sonst
gesperrt. Bind auf Port 0 (Quellport, zufälliger Lauscher) bleibt frei, connect bindet implizit Port 0.

Fail-closed: Ohne Landlock ab ABI 6 (Kernel 6.12) startet der Befehl nicht (Exit 126). UDP (DNS) bleibt frei;
der Ausgangsfilter (nft) begrenzt sie auf DNS. Nur Standardbibliothek (ctypes).
"""

from __future__ import annotations

import ctypes
import os
import sys

SYS_LANDLOCK_CREATE_RULESET = 444  # gleiche Nummern auf x86_64 und arm64 (generische Tabelle)
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_ACCESS_NET_BIND_TCP = 1 << 0
LANDLOCK_ACCESS_NET_CONNECT_TCP = 1 << 1
LANDLOCK_RULE_NET_PORT = 2
LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0
LANDLOCK_SCOPE_SIGNAL = 1 << 1
PR_SET_NO_NEW_PRIVS = 38
MIN_ABI = 6
EXIT_FEHLER = 126
BIND_MIN, BIND_MAX = 20000, 20999  # M3: nur diese Ports darf ein Auftrag binden (eigene Entwicklungsserver)


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


class NetPortAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64)]


def parse_ports(text: str) -> set[int]:
    """'22,5432, 11434' -> {22, 5432, 11434}; Unsinn wirft ValueError (fail-closed)."""
    ports = set()
    for part in text.replace(" ", ",").split(","):
        if not part:
            continue
        port = int(part)
        if not 1 <= port <= 65535:
            raise ValueError(f"Port außerhalb 1..65535: {port}")
        ports.add(port)
    return ports


def erlaubte_ports(gesperrt: set[int]) -> list[int]:
    return [p for p in range(1, 65536) if p not in gesperrt]


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def _check(rc: int, was: str) -> int:
    if rc < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"{was}: {os.strerror(err)}")
    return rc


def abi_version() -> int:
    libc = _libc()
    return libc.syscall(ctypes.c_long(SYS_LANDLOCK_CREATE_RULESET), ctypes.c_void_p(None), ctypes.c_size_t(0),
                        ctypes.c_uint32(LANDLOCK_CREATE_RULESET_VERSION))


def port_zugriff(gesperrt: set[int]) -> dict[int, int]:
    """Je Port der erlaubte Zugriff: CONNECT für alle nicht gesperrten Ports, BIND nur für BIND_MIN..BIND_MAX.
    Ports ohne Zugriff kommen nicht vor (connect dorthin und bind sonst wo sind damit gesperrt). Bind auf Port 0
    (Quellport, zufälliger Lauscher) lässt Landlock ohnehin immer zu, connect bindet implizit Port 0."""
    zugriff: dict[int, int] = {}
    for p in range(1, 65536):
        acc = 0
        if p not in gesperrt:
            acc |= LANDLOCK_ACCESS_NET_CONNECT_TCP
        if BIND_MIN <= p <= BIND_MAX:
            acc |= LANDLOCK_ACCESS_NET_BIND_TCP
        if acc:
            zugriff[p] = acc
    return zugriff


def abschotten(gesperrt: set[int]) -> None:
    libc = _libc()
    abi = abi_version()
    if abi < MIN_ABI:
        raise OSError(f"Landlock ABI {abi} zu alt oder nicht aktiv (nötig: {MIN_ABI})")
    attr = RulesetAttr(0, LANDLOCK_ACCESS_NET_BIND_TCP | LANDLOCK_ACCESS_NET_CONNECT_TCP,
                       LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET | LANDLOCK_SCOPE_SIGNAL)
    fd = _check(libc.syscall(ctypes.c_long(SYS_LANDLOCK_CREATE_RULESET), ctypes.byref(attr),
                             ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint32(0)), "create_ruleset")
    try:
        rule = NetPortAttr(0, 0)
        for port, acc in port_zugriff(gesperrt).items():
            rule.allowed_access = acc
            rule.port = port
            _check(libc.syscall(ctypes.c_long(SYS_LANDLOCK_ADD_RULE), ctypes.c_int(fd),
                                ctypes.c_int(LANDLOCK_RULE_NET_PORT), ctypes.byref(rule), ctypes.c_uint32(0)),
                   "add_rule")
        # Port 0 (bind -> zufälliger freier Port) gesondert und tolerant: manche Kernel lehnen eine Regel für
        # Port 0 ab. Dann bleibt bind(0) gesperrt; das ist unkritisch (der Port lässt sich nicht gezielt wählen).
        rule.allowed_access = LANDLOCK_ACCESS_NET_BIND_TCP
        rule.port = 0
        libc.syscall(ctypes.c_long(SYS_LANDLOCK_ADD_RULE), ctypes.c_int(fd),
                     ctypes.c_int(LANDLOCK_RULE_NET_PORT), ctypes.byref(rule), ctypes.c_uint32(0))
        _check(libc.prctl(ctypes.c_int(PR_SET_NO_NEW_PRIVS), ctypes.c_ulong(1), ctypes.c_ulong(0),
                          ctypes.c_ulong(0), ctypes.c_ulong(0)), "no_new_privs")
        _check(libc.syscall(ctypes.c_long(SYS_LANDLOCK_RESTRICT_SELF), ctypes.c_int(fd), ctypes.c_uint32(0)),
               "restrict_self")
    finally:
        os.close(fd)


# ---------------------------------------------------------------- seccomp (cBPF für bwrap --seccomp)
# Wird bei der Installation erzeugt (python3 abschottung.py --seccomp-bpf > waechter/seccomp.bpf) und von
# wamo-job.sh über einen Dateideskriptor an bwrap gegeben. Abgewiesen (EPERM) werden Systemaufrufe mit großer
# Kernel-Angriffsfläche, die ein Entwicklungsauftrag nicht braucht; clone3 bekommt ENOSYS (glibc fällt dann
# auf clone zurück). TCP Fast Open (MSG_FASTOPEN in sendto, sendmsg, sendmmsg) baut Verbindungen ohne
# connect() auf und umginge so die Landlock-Portsperre; gemessen auf Sokrates-2: EINPROGRESS statt EACCES.
AUDIT_ARCH = {"aarch64": 0xC00000B7, "x86_64": 0xC000003E}
SYSCALLS = {  # Name: (aarch64 = generische Tabelle, x86_64)
    "io_uring_setup": (425, 425), "io_uring_enter": (426, 426), "io_uring_register": (427, 427),
    "keyctl": (219, 250), "add_key": (217, 248), "request_key": (218, 249), "bpf": (280, 321),
    "perf_event_open": (241, 298), "userfaultfd": (282, 323), "kcmp": (272, 312),
    "process_vm_readv": (270, 310), "process_vm_writev": (271, 311), "open_by_handle_at": (265, 304),
    "name_to_handle_at": (264, 303), "unshare": (97, 272), "setns": (268, 308), "init_module": (105, 175),
    "finit_module": (273, 313), "delete_module": (106, 176), "kexec_load": (104, 246),
    "kexec_file_load": (294, 320), "mount": (40, 165), "umount2": (39, 166), "pivot_root": (41, 155),
    "open_tree": (428, 428), "move_mount": (429, 429), "fsopen": (430, 430), "fsconfig": (431, 431),
    "fsmount": (432, 432), "fspick": (433, 433), "mount_setattr": (442, 442),
}
ENOSYS_SYSCALLS = {"clone3": (435, 435)}
SEND_SYSCALLS = {"sendto": ((206, 44), 3), "sendmsg": ((211, 46), 2), "sendmmsg": ((269, 307), 3)}  # (Nummern, Index flags)
MSG_FASTOPEN = 0x20000000
# socket(): Allowlist der Familien (M2). Nur AF_UNIX, AF_INET, AF_INET6 und AF_NETLINK (nur Protokoll 0,
# NETLINK_ROUTE für getaddrinfo) sind erlaubt; Bluetooth, QIPCRTR, MCTP, AF_PACKET, AF_VSOCK, AF_ALG, XDP und
# andere Netlink-Protokolle (sock_diag, netfilter, audit, uevent) fallen weg. Innerhalb AF_INET/INET6 bleiben
# MPTCP (262) und SCTP (132) gesperrt: MPTCP fällt bei connect auf TCP zurück und umginge die Landlock-Portsperre
# (auf Sokrates-2 nachgewiesen), SCTP ebenso, sobald das Modul nachlädt.
SOCKET_NR = (198, 41)
AF_UNIX, AF_INET, AF_INET6, AF_NETLINK = 1, 2, 10, 16
SOCKET_FAMILIEN_ERLAUBT = (AF_UNIX, AF_INET, AF_INET6, AF_NETLINK)
NETLINK_PROTO_ERLAUBT = 0  # NETLINK_ROUTE
SOCKET_PROTOKOLLE_GESPERRT = {"IPPROTO_MPTCP": 262, "IPPROTO_SCTP": 132}
EPERM, ENOSYS = 1, 38
RET_ALLOW, RET_ERRNO, RET_KILL_PROCESS = 0x7FFF0000, 0x00050000, 0x80000000
LD_W_ABS, JEQ_K, JGE_K, JSET_K, RET_K = 0x20, 0x15, 0x35, 0x45, 0x06  # BPF_LD|W|ABS, BPF_JMP|JEQ|K, ...


def _mit_labels(zeilen: list) -> list[tuple[int, int, int, int]]:
    """Kleiner Assembler: ('label', name) markiert eine Stelle, (code, jt, jf, k) ist ein Befehl; jt/jf dürfen
    Label-Namen (Strings) sein und werden in Vorwärts-Offsets aufgelöst (relativ zum nächsten Befehl)."""
    pos: dict[str, int] = {}
    befehle = []
    for z in zeilen:
        if z[0] == "label":
            pos[z[1]] = len(befehle)
        else:
            befehle.append(z)
    out = []
    for i, (code, jt, jf, k) in enumerate(befehle):
        aufl = lambda t: pos[t] - (i + 1) if isinstance(t, str) else t  # noqa: E731
        out.append((code, aufl(jt), aufl(jf), k))
    return out


def _socket_block() -> list[tuple[int, int, int, int]]:
    """In sich geschlossener Block für den socket()-Aufruf: Familien-Allowlist, AF_NETLINK nur Protokoll 0,
    AF_INET/INET6 ohne MPTCP/SCTP. A enthält beim Eintritt die Syscall-Nummer (hier nicht mehr gebraucht)."""
    eperm = (RET_K, 0, 0, RET_ERRNO | EPERM)
    erlaubt = (RET_K, 0, 0, RET_ALLOW)
    mptcp, sctp = SOCKET_PROTOKOLLE_GESPERRT["IPPROTO_MPTCP"], SOCKET_PROTOKOLLE_GESPERRT["IPPROTO_SCTP"]
    return _mit_labels([  # nur Vorwärtssprünge (cBPF kann nicht rückwärts springen)
        (LD_W_ABS, 0, 0, 16),                       # A = domain (args[0])
        (JEQ_K, "erlaubt1", 0, AF_UNIX),
        (JEQ_K, "inet", 0, AF_INET),
        (JEQ_K, "inet", 0, AF_INET6),
        (JEQ_K, "netlink", 0, AF_NETLINK),
        eperm,                                       # jede andere Familie
        ("label", "inet"),
        (LD_W_ABS, 0, 0, 32),                        # A = protocol (args[2])
        (JEQ_K, "eperm2", 0, mptcp),
        (JEQ_K, "eperm2", 0, sctp),
        ("label", "erlaubt1"),
        erlaubt,
        ("label", "eperm2"),
        eperm,
        ("label", "netlink"),
        (LD_W_ABS, 0, 0, 32),
        (JEQ_K, "erlaubt2", 0, NETLINK_PROTO_ERLAUBT),
        eperm,
        ("label", "erlaubt2"),
        erlaubt,
    ])


def seccomp_programm(arch: str) -> list[tuple[int, int, int, int]]:
    """cBPF-Anweisungen (code, jt, jf, k) für struct seccomp_data {nr, arch, ip, args[6]}."""
    idx = 0 if arch == "aarch64" else 1
    prog = [(LD_W_ABS, 0, 0, 4), (JEQ_K, 1, 0, AUDIT_ARCH[arch]), (RET_K, 0, 0, RET_KILL_PROCESS),
            (LD_W_ABS, 0, 0, 0)]
    if arch == "x86_64":  # x32-Aufrufe (nr ab 0x40000000) gar nicht erst zulassen
        prog += [(JGE_K, 0, 1, 0x40000000), (RET_K, 0, 0, RET_KILL_PROCESS)]
    for nummern in SYSCALLS.values():
        prog += [(JEQ_K, 0, 1, nummern[idx]), (RET_K, 0, 0, RET_ERRNO | EPERM)]
    for nummern in ENOSYS_SYSCALLS.values():
        prog += [(JEQ_K, 0, 1, nummern[idx]), (RET_K, 0, 0, RET_ERRNO | ENOSYS)]
    block = _socket_block()  # socket(): Familien-Allowlist (M2)
    prog += [(JEQ_K, 0, len(block), SOCKET_NR[idx]), *block]
    for nummern, flags_index in SEND_SYSCALLS.values():
        # nicht dieser Aufruf: 4 weiter; sonst flags (unteres Wort, little endian) auf MSG_FASTOPEN prüfen
        prog += [(JEQ_K, 0, 4, nummern[idx]), (LD_W_ABS, 0, 0, 16 + 8 * flags_index),
                 (JSET_K, 0, 1, MSG_FASTOPEN), (RET_K, 0, 0, RET_ERRNO | EPERM), (RET_K, 0, 0, RET_ALLOW)]
    prog.append((RET_K, 0, 0, RET_ALLOW))
    return prog


def seccomp_bpf(arch: str | None = None) -> bytes:
    import platform
    import struct
    arch = arch or platform.machine()
    if arch not in AUDIT_ARCH:
        raise OSError(f"seccomp-Filter für Architektur {arch} nicht vorhanden")
    return b"".join(struct.pack("<HBBI", *ins) for ins in seccomp_programm(arch))


def parse_args(argv: list[str]) -> tuple[set[int], list[str]]:
    if "--" not in argv:
        raise ValueError("Aufruf: abschottung.py --sperrports LISTE -- befehl ...")
    i = argv.index("--")
    opts, cmd = argv[:i], argv[i + 1:]
    if len(opts) != 2 or opts[0] != "--sperrports" or not cmd:
        raise ValueError("Aufruf: abschottung.py --sperrports LISTE -- befehl ...")
    return parse_ports(opts[1]), cmd


def pdeathsig_setzen() -> None:
    """Stirbt der übergeordnete Prozess (bwrap, letztlich die Kette bis zum Dispatcher), bekommt dieser Prozess
    SIGKILL (N10, best effort). Deckt den häufigen Fall ab; ein hartes SIGKILL des Dispatchers selbst fängt
    zusätzlich das Zeitlimit (RuntimeMaxSec) der systemd-Einheit ab."""
    try:
        PR_SET_PDEATHSIG, SIGKILL = 1, 9
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0)
    except (OSError, AttributeError, ValueError, TypeError):
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--seccomp-bpf"]:  # Installation: Filter erzeugen
        try:
            sys.stdout.buffer.write(seccomp_bpf(argv[1] if len(argv) > 1 else None))
        except OSError as e:
            print(f"WAMO-Abschottung: {e}", file=sys.stderr)
            return EXIT_FEHLER
        return 0
    try:
        gesperrt, cmd = parse_args(argv)
        pdeathsig_setzen()
        abschotten(gesperrt)
    except (OSError, ValueError) as e:
        print(f"WAMO-Abschottung fehlgeschlagen, Auftrag startet nicht: {e}", file=sys.stderr)
        return EXIT_FEHLER
    try:
        os.execvp(cmd[0], cmd)
    except OSError as e:
        print(f"WAMO-Abschottung: {cmd[0]} nicht startbar ({e.strerror})", file=sys.stderr)
        return EXIT_FEHLER
    return EXIT_FEHLER  # nicht erreichbar


if __name__ == "__main__":
    sys.exit(main())
