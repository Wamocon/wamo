; Zusätze zum Installer (electron-builder, NSIS, pro Benutzer, ohne Adminrechte).
;
; Deinstallation und Aktualisierung: Die geplante Aufgabe "WAMO Tunnel" (Host und SSH-Rücktunnel) läuft aus dem
; Installationsordner. Vorher beenden und entfernen, sonst halten laufende Programme Dateien fest. Nach einer
; Aktualisierung legt die Geräteeinrichtung der App die Aufgabe bei der nächsten Anmeldung wieder an.
; Schlüssel, Token und Gerätedaten (%LOCALAPPDATA%\WAMO, %USERPROFILE%\.ssh) bleiben bewusst stehen.
; Der Deinstaller ist ein 32-Bit-Programm: ohne Abschalten der Umleitung liefe eine 32-Bit-PowerShell (SysWOW64).
!include "x64.nsh"
; Sandbox-Zugriff auf den Installationsordner: Im Ordner Programs eines Benutzers tragen oft einzelne Windows-App-Pakete
; Rechte (S-1-15-2-...), aber nicht "ALLE ANWENDUNGSPAKETE". Dann verweigert Windows dem Sandbox-Prozess von Electron das
; Lesen, und die App bricht beim Start mit einem Fatal-Fehler ab (install_dir_access.cc). Der Benutzer ist Besitzer seines
; Ordners, deshalb braucht das keine Adminrechte. Nur dieser Ordner, nur Lesen und Ausführen.
!macro customInstall
  nsExec::Exec '"$SYSDIR\icacls.exe" "$INSTDIR" /grant *S-1-15-2-1:(OI)(CI)(RX)'
  Pop $0
!macroend

!macro customUnInstall
  ${DisableX64FSRedirection}
  IfFileExists "$INSTDIR\resources\scripts\wamo-tunnel-entfernen.ps1" 0 +2
  nsExec::Exec '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\scripts\wamo-tunnel-entfernen.ps1"'
  ${EnableX64FSRedirection}
!macroend
