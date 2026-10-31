; Zusätze zum Installer (electron-builder, NSIS, pro Benutzer, ohne Adminrechte).
;
; Deinstallation und Aktualisierung: Die geplante Aufgabe "WAMO Tunnel" (Host und SSH-Rücktunnel) läuft aus dem
; Installationsordner. Vorher beenden und entfernen, sonst halten laufende Programme Dateien fest. Nach einer
; Aktualisierung legt die Geräteeinrichtung der App die Aufgabe bei der nächsten Anmeldung wieder an.
; Schlüssel, Token und Gerätedaten (%LOCALAPPDATA%\WAMO, %USERPROFILE%\.ssh) bleiben bewusst stehen.
!macro customUnInstall
  IfFileExists "$INSTDIR\resources\scripts\wamo-tunnel-entfernen.ps1" 0 +2
  nsExec::Exec '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\scripts\wamo-tunnel-entfernen.ps1"'
!macroend
