"""Startverknüpfungen (.lnk) und der Desktop-Ordner für das Platz-Skript (nur Standardbibliothek, Windows).

Ohne PowerShell und ohne Zusatzpaket: Die Shell wird direkt über COM angesprochen (IShellLinkW und IPersistFile, per ctypes). Das ist
der Weg, den auch Windows selbst für Verknüpfungen benutzt, ein von Hand gebautes .lnk-Format wäre fragiler. Es wird nur eine Datei
geschrieben, die Registrierung bleibt unberührt.

Nicht ohne echten Windows-Rechner prüfbar: Die Tests ersetzen `verknuepfung_schreiben` durch eine Attrappe.
"""
from __future__ import annotations

import ctypes
import os
# HRESULT, WINFUNCTYPE und OleDLL gibt es nur unter Windows und werden erst beim Aufruf benutzt
from ctypes import POINTER, byref, c_int, c_ulong, c_void_p, c_wchar_p
from pathlib import Path

CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
# Plätze in der Methodentabelle (vtable) von IShellLinkW und IPersistFile
QUERY_INTERFACE, RELEASE = 0, 2
SET_DESCRIPTION, SET_WORKING_DIRECTORY, SET_ARGUMENTS, SET_SHOW_CMD, SET_PATH = 7, 9, 11, 15, 20
PERSIST_SAVE = 6
SW_SHOWNORMAL = 1


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    wert = _GUID()
    ctypes.OleDLL("ole32").CLSIDFromString(text, byref(wert))
    return wert


def _methode(zeiger: c_void_p, platz: int, *argtypen):
    tabelle = ctypes.cast(ctypes.cast(zeiger, POINTER(c_void_p))[0], POINTER(c_void_p))
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypen)(tabelle[platz])


def _freigeben(zeiger: c_void_p) -> None:
    tabelle = ctypes.cast(ctypes.cast(zeiger, POINTER(c_void_p))[0], POINTER(c_void_p))
    ctypes.WINFUNCTYPE(c_ulong, c_void_p)(tabelle[RELEASE])(zeiger)


def verknuepfung_schreiben(pfad: Path, ziel: str, argumente: str, arbeitsordner: str) -> None:
    """Legt eine Verknüpfung an (Ziel, Argumente, Arbeitsordner). Ein Fehler der Shell wird zu OSError."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitialize(None)
    link, datei = c_void_p(), c_void_p()
    try:
        ole32.CoCreateInstance(byref(_guid(CLSID_SHELL_LINK)), None, 1, byref(_guid(IID_SHELL_LINK_W)), byref(link))
        _methode(link, SET_PATH, c_wchar_p)(link, ziel)
        _methode(link, SET_ARGUMENTS, c_wchar_p)(link, argumente)
        _methode(link, SET_WORKING_DIRECTORY, c_wchar_p)(link, arbeitsordner)
        _methode(link, SET_DESCRIPTION, c_wchar_p)(link, "W.A.I.S. Testlabor")
        _methode(link, SET_SHOW_CMD, c_int)(link, SW_SHOWNORMAL)
        _methode(link, QUERY_INTERFACE, POINTER(_GUID), POINTER(c_void_p))(link, byref(_guid(IID_PERSIST_FILE)), byref(datei))
        _methode(datei, PERSIST_SAVE, c_wchar_p, c_int)(datei, str(pfad), 1)
    finally:
        for zeiger in (datei, link):
            if zeiger.value:
                _freigeben(zeiger)
        ole32.CoUninitialize()


def desktop_ordner() -> Path:
    """Der Desktop des Benutzers (auch bei umgeleitetem Ordner), sonst %USERPROFILE%\\Desktop."""
    try:
        ole32, shell32 = ctypes.OleDLL("ole32"), ctypes.OleDLL("shell32")
        pfad = c_void_p()
        shell32.SHGetKnownFolderPath(byref(_guid(FOLDERID_DESKTOP)), 0, None, byref(pfad))
        try:
            return Path(ctypes.wstring_at(pfad.value))
        finally:
            ole32.CoTaskMemFree(pfad)
    except (OSError, AttributeError, ValueError, TypeError):
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
