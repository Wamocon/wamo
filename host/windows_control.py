"""Fenster, Tastatur und installierte Programme für den WAMO-Host (nur Windows).

Sicherheitsregeln (hier, direkt vor der Aktion):
  * Tippen und Tastenkuerzel NIE in Konsolen, Registry, Admin-/Anmeldefenster oder Passwort-Manager:
    dort koennten getippte Zeichen Befehle ausfuehren oder Zugangsdaten beruehren.
  * Keine Tastenkuerzel mit der Windows-Taste (Win+R oeffnet "Ausfuehren").
  * Bei gesperrtem Bildschirm (is_locked) keine Eingaben, Klicks und Fotos; das prüft wamo_host.py vorab.
  * Programme nur aus dem Startmenue des Nutzers, keine Shells/Registry/Admin-Werkzeuge.
  * Laengenlimits fuer Text.
"""

from __future__ import annotations

import ctypes
import functools
import json
import re
import subprocess
import time
from collections.abc import Callable
from ctypes import wintypes

MAX_TYPE_CHARS = 2000
OVERLAY_TITLE = "WAMO Stimmen-Rand"  # Fenstertitel aus desktop/overlay.html (Namensvertrag)

BLOCKED_TARGET_EXES = {
    "powershell.exe", "pwsh.exe", "powershell_ise.exe", "cmd.exe", "windowsterminal.exe", "wt.exe",
    "conhost.exe", "openconsole.exe", "regedit.exe", "mmc.exe", "taskmgr.exe", "consent.exe",
    "credentialuibroker.exe", "lsass.exe", "mstsc.exe", "keepass.exe", "keepassxc.exe",
    "1password.exe", "bitwarden.exe", "dashlane.exe", "gpedit.exe",
    # weitere Shells und Terminals: getippter Text würde dort als Befehl laufen
    "bash.exe", "wsl.exe", "wslhost.exe", "mintty.exe", "git-bash.exe", "putty.exe", "kitty.exe",
    "ssh.exe", "alacritty.exe", "wezterm-gui.exe", "hyper.exe", "tabby.exe", "conemu64.exe",
    "conemu.exe", "cmder.exe", "python.exe", "pythonw.exe", "node.exe",
    # Startmenü und Suche starten getippte Programmnamen
    "searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe",
}
# Tippen und Tasten (nicht Klicks) zusätzlich gesperrt: in der Explorer-Adressleiste laufen getippte
# Befehle ("cmd", "powershell ...") direkt.
TYPE_BLOCKED_EXES = {"explorer.exe"}
# Mail- und Messenger-Fenster: Senden (Enter, Strg+Enter, Alt+S) braucht eine Freigabe.
MESSAGING_EXES = {
    "outlook.exe", "olk.exe", "hxoutlook.exe", "ms-teams.exe", "teams.exe", "whatsapp.exe", "slack.exe",
    "thunderbird.exe", "signal.exe", "telegram.exe", "discord.exe", "mailspring.exe",
    "zoom.exe", "mattermost.exe", "element.exe", "skype.exe", "rocket.chat.exe", "wire.exe", "threema.exe",
    "messenger.exe",
}
MESSAGING_TITLE = re.compile(
    r"(outlook|teams|whatsapp|slack|thunderbird|signal|telegram|discord|e-mail|\bmail\b|posteingang|"
    r"inbox|nachricht verfassen|neue nachricht|google chat|mattermost|messenger|rocket\.chat|threema)",
    re.IGNORECASE,
)
# Die eigene Oberfläche (Electron-App "WAMO", Browser-Tab "WAMO - ...") bedient WAMO nie: dort liegen die
# Freigabekarten. Groß geschrieben und allein stehend, damit "wamo - Visual Studio Code" oder "WAMO-Konzept.docx"
# weiter gehen.
WAMO_UI_EXES = {"electron.exe"}
WAMO_UI_TITLE = re.compile(r"^WAMO(?![-.\w])")
# Adressleiste von Browsern und Explorer: Strg+L, Alt+D, F6
ADDRESS_BAR_COMBOS = {frozenset({0x11, ord("L")}), frozenset({0x12, ord("D")}), frozenset({0x75})}
# Senden in Mail- und Messenger-Fenstern: Enter, Strg+Enter, Strg+Umschalt+Enter, Alt+S, Leertaste (auf "Senden")
SEND_COMBOS = {frozenset({0x0D}), frozenset({0x11, 0x0D}), frozenset({0x11, 0x10, 0x0D}), frozenset({0x12, ord("S")}),
               frozenset({0x20})}


def is_messaging(exe: str, title: str) -> bool:
    return (exe or "").lower() in MESSAGING_EXES or bool(MESSAGING_TITLE.search(title or ""))


def check_not_wamo_ui(exe: str, title: str) -> None:
    """Wirft ValueError für die eigene Oberfläche (Freigabekarten): dort gibt nur ein Mensch frei."""
    if (exe or "").lower() in WAMO_UI_EXES or WAMO_UI_TITLE.search(title or ""):
        raise ValueError("In der eigenen Oberfläche tippt und klickt WAMO nicht (Freigaben gibt nur ein Mensch)")


def check_input_target(exe: str, title: str) -> None:
    """Für Eingaben (Klicken, Scrollen): wie check_target, zusätzlich nie in der eigenen Oberfläche."""
    check_target(exe, title)
    check_not_wamo_ui(exe, title)


def check_typing_target(exe: str, title: str) -> None:
    """Wie check_input_target, zusätzlich ohne Explorer (Adressleiste führt Befehle aus)."""
    check_input_target(exe, title)
    if (exe or "").lower() in TYPE_BLOCKED_EXES:
        raise ValueError("Im Explorer tippt WAMO nicht (die Adressleiste führt Befehle aus)")


BLOCKED_TARGET_TITLE = re.compile(
    r"(administrator|ausf(ü|ue)hren$|^run$|kennwort|passwort|password|anmeld|sign in|"
    r"benutzerkontensteuerung|user account control|windows-sicherheit|windows security)",
    re.IGNORECASE,
)
BLOCKED_APP_NAMES = re.compile(
    r"(powershell|eingabeaufforderung|command prompt|registrierungs|registry|regedit|"
    r"computerverwaltung|gruppenrichtlinie|dienste|services|task-manager|ausf(ü|ue)hren|"
    r"windows-tools|administrative|systemkonfiguration)",
    re.IGNORECASE,
)

# ---------- Tasten ----------
VK = {
    "ctrl": 0x11, "strg": 0x11, "shift": 0x10, "alt": 0x12,
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "leertaste": 0x20,
    "backspace": 0x08, "delete": 0x2E, "entf": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(c): ord(chr(c).upper()) for c in range(ord("a"), ord("z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
}
MODIFIERS = {"ctrl", "strg", "shift", "alt"}


def parse_combo(combo: object) -> list[int]:
    """'ctrl+t', 'enter', 'ctrl+shift+n' -> virtuelle Tastencodes. Windows-Taste ist nicht erlaubt."""
    if not isinstance(combo, str) or not combo.strip() or len(combo) > 40:
        raise ValueError("ungueltige Tastenkombination")
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if any(p in ("win", "windows", "super", "meta", "cmd") for p in parts):
        raise ValueError("Tastenkuerzel mit der Windows-Taste sind gesperrt")
    if not parts or any(p not in VK for p in parts):
        raise ValueError(f"unbekannte Taste in {combo!r}")
    if sum(1 for p in parts if p not in MODIFIERS) != 1:
        raise ValueError("genau eine Haupttaste, z. B. ctrl+t")
    if any(p in ("ctrl", "strg") for p in parts) and any(p in ("esc", "escape") for p in parts):
        raise ValueError("ctrl+esc öffnet das Startmenü und ist gesperrt")
    codes = [VK[p] for p in parts]
    if frozenset(codes) in ADDRESS_BAR_COMBOS:
        raise ValueError("Tastenkürzel für die Adressleiste (Strg+L, Alt+D, F6) sind gesperrt")
    return codes


def check_target(exe: str, title: str) -> None:
    """Wirft ValueError, wenn in dieses Fenster nicht getippt werden darf."""
    if not (exe or "").strip():
        # Programm nicht ermittelbar (z. B. Fenster mit Admin-Rechten): lieber nicht tippen
        raise ValueError("Zielprogramm unbekannt, WAMO tippt dort nicht")
    if (exe or "").lower() in BLOCKED_TARGET_EXES:
        raise ValueError(f"In {exe} tippt WAMO nicht (Konsole/Systemwerkzeug)")
    if BLOCKED_TARGET_TITLE.search(title or ""):
        raise ValueError("In Anmelde-, Admin- oder Passwortfenster tippt WAMO nicht")


def check_app_name(name: str) -> None:
    if BLOCKED_APP_NAMES.search(name):
        raise ValueError(f"{name} startet WAMO nicht (Systemwerkzeug)")


# ---------- Windows-API ----------
def _win32():
    import win32con  # pywin32, nur unter Windows
    import win32gui
    import win32process
    return win32gui, win32process, win32con


def _exe_of(hwnd: int) -> str:
    _, win32process, _ = _win32()
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _title(hwnd: int) -> str:
    """Fenstertitel als Unicode (win32gui.GetWindowText liefert ANSI und verliert Umlaute)."""
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def list_windows() -> list[dict]:
    """Sichtbare Hauptfenster mit Titel und Programm."""
    win32gui, _, win32con = _win32()
    found: list[dict] = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        title = _title(hwnd)
        if not title.strip() or win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return True
        if title == OVERLAY_TITLE:  # eigene Rand-Fenster der Desktop-App
            return True
        found.append({"hwnd": hwnd, "titel": title[:200], "programm": _exe_of(hwnd)})
        return True

    win32gui.EnumWindows(visit, None)
    return found


def find_window(query: str) -> dict:
    q = (query or "").strip().lower()
    if not q:
        raise ValueError("Fenstername fehlt")
    candidates = [w for w in list_windows()
                  if q in w["titel"].lower() or q in w["programm"].lower().removesuffix(".exe")]
    if not candidates:
        raise ValueError(f"Kein offenes Fenster passt zu {query!r}")
    return candidates[0]


def window_info(hwnd: int) -> dict:
    """Titel und Programm eines Fensters; ValueError, wenn es das Fenster nicht mehr gibt."""
    win32gui, _, _ = _win32()
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise ValueError("Das Fenster gibt es nicht mehr")
    return {"hwnd": hwnd, "titel": _title(hwnd), "programm": _exe_of(hwnd)}


def foreground() -> dict:
    win32gui, _, _ = _win32()
    hwnd = win32gui.GetForegroundWindow()
    return {"hwnd": hwnd, "titel": _title(hwnd), "programm": _exe_of(hwnd)}


def focus(hwnd: int) -> None:
    win32gui, _, win32con = _win32()
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    # Windows erlaubt SetForegroundWindow nur nach einer Eingabe; ein Alt-Druck loest die Sperre
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.25)


# SendInput-Strukturen
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send(inputs: list[_INPUT]) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    i = _INPUT(type=1)
    i.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return i


TYPE_RECHECK_CHARS = 100  # so oft prüft type_text, ob das Zielfenster noch dasselbe ist


def type_text(text: str, recheck: Callable[[], None] | None = None) -> int:
    """Tippt text. recheck wird vor jedem Block von TYPE_RECHECK_CHARS Zeichen und vor jedem Enter aufgerufen und
    wirft ValueError, wenn das Zielfenster gewechselt hat; dann bricht das Tippen ab. Gibt die Anzahl der
    getippten Zeichen zurück. Zeichen außerhalb der BMP (Emoji) gehen als UTF-16-Surrogatpaar."""
    KEYUP, UNICODE = 0x0002, 0x0004
    for i, ch in enumerate(text):
        if recheck is not None and (i % TYPE_RECHECK_CHARS == 0 or ch == "\n"):
            recheck()
        if ch == "\n":
            _send([_key(vk=0x0D), _key(vk=0x0D, flags=KEYUP)])
        else:
            units = ch.encode("utf-16-le")
            for j in range(0, len(units), 2):
                code = int.from_bytes(units[j:j + 2], "little")
                _send([_key(scan=code, flags=UNICODE), _key(scan=code, flags=UNICODE | KEYUP)])
        time.sleep(0.004)
    return len(text)


def press(codes: list[int]) -> None:
    KEYUP = 0x0002
    _send([_key(vk=c) for c in codes] + [_key(vk=c, flags=KEYUP) for c in reversed(codes)])
    time.sleep(0.05)


# ---------- Sperre ----------
DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2


@functools.cache
def _desktop_api():
    """Eigene user32-Instanz mit Prototypen (die geteilte ctypes.windll bleibt unverändert)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD)]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    return user32


def is_locked(api=None) -> bool:
    """Ist der Bildschirm gesperrt (Win+L, Anmeldebildschirm, UAC-Dialog auf dem sicheren Desktop)? Dann ist der
    Eingabedesktop nicht "Default": OpenInputDesktop scheitert für normale Prozesse, oder der Desktop heißt anders
    (Winlogon). Tippen, Klicken und Bildschirmfotos liefen dort ins Leere. Jeder Fehler gilt als gesperrt."""
    try:
        user32 = api if api is not None else _desktop_api()
        desk = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    except (AttributeError, OSError):
        return True
    if not desk:
        return True
    try:
        name = ctypes.create_unicode_buffer(256)
        noetig = wintypes.DWORD(0)
        if not user32.GetUserObjectInformationW(desk, UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(noetig)):
            return True
        return name.value.lower() != "default"
    finally:
        user32.CloseDesktop(desk)


# ---------- Bildschirm und Maus ----------
MAX_IMAGE_EDGE = 1568  # Kantenlaenge, die Claude-Bildmodelle ohne Verkleinern verarbeiten


def _dpi_aware() -> None:
    """Pixelgenaue Koordinaten auf skalierten Monitoren (sonst passen Foto und Klick nicht zusammen)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per Monitor
    except (AttributeError, OSError):
        pass


def monitor_of_foreground() -> int:
    """Index (mss, ab 1) des Monitors mit dem Fenster im Vordergrund."""
    import mss
    _dpi_aware()
    win32gui, _, _ = _win32()
    left, top, right, bottom = win32gui.GetWindowRect(win32gui.GetForegroundWindow())
    cx, cy = (left + right) // 2, (top + bottom) // 2
    with mss.mss() as sct:
        for i, m in enumerate(sct.monitors[1:], start=1):
            if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                return i
    return 1


def screenshot(monitor: int = 0) -> dict:
    """JPEG eines Monitors (0 = der mit dem Fenster vorne) plus Umrechnung Bild -> Bildschirm."""
    import base64
    import io

    import mss
    from PIL import Image

    _dpi_aware()
    with mss.mss() as sct:
        index = monitor or monitor_of_foreground()
        if not 1 <= index < len(sct.monitors):
            raise ValueError(f"Monitor {monitor} gibt es nicht (1 bis {len(sct.monitors) - 1})")
        m = sct.monitors[index]
        raw = sct.grab(m)
    img = Image.frombytes("RGB", raw.size, raw.rgb)
    scale = min(1.0, MAX_IMAGE_EDGE / max(img.size))
    if scale < 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return {
        "monitor": index, "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"],
        "scale": scale, "bild_breite": img.width, "bild_hoehe": img.height,
        "jpeg_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
    }


def window_at(x: int, y: int) -> dict:
    """Hauptfenster unter einem Bildschirmpunkt (fuer die Sicherheitspruefung vor Klicks)."""
    win32gui, _, _ = _win32()
    hwnd = win32gui.WindowFromPoint((x, y))
    root = ctypes.windll.user32.GetAncestor(hwnd, 2)  # GA_ROOT
    return {"hwnd": root, "titel": _title(root), "programm": _exe_of(root)}


def screen_bounds() -> tuple[int, int, int, int]:
    import mss
    _dpi_aware()
    with mss.mss() as sct:
        v = sct.monitors[0]  # alle Monitore zusammen
        return v["left"], v["top"], v["left"] + v["width"], v["top"] + v["height"]


def click(x: int, y: int, button: str = "links", double: bool = False) -> None:
    _dpi_aware()
    down, up = {"links": (0x0002, 0x0004), "rechts": (0x0008, 0x0010)}[button]
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    for _ in range(2 if double else 1):
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.06)


def scroll(x: int, y: int, steps: int) -> None:
    """steps > 0 hoch, < 0 runter (je 120 = eine Raste)."""
    _dpi_aware()
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(steps) * 120, 0)


def start_menu_apps() -> list[dict]:
    p = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
         "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    data = json.loads(p.stdout or "[]")
    return data if isinstance(data, list) else [data]


def pick_start_app(name: str, apps: list[dict]) -> dict:
    q = (name or "").strip().lower()
    if not q:
        raise ValueError("Programmname fehlt")
    exact = [a for a in apps if a["Name"].lower() == q]
    partial = sorted((a for a in apps if q in a["Name"].lower()), key=lambda a: len(a["Name"]))
    match = (exact or partial or [None])[0]
    if not match:
        raise ValueError(f"Kein installiertes Programm passt zu {name!r}")
    check_app_name(match["Name"])
    return match


def launch_start_app(app_id: str) -> None:
    # explorer.exe mit AppsFolder-Pfad startet Store- und klassische Apps; argv ohne Shell
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], close_fds=True)
