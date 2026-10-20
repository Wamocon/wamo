@echo off
rem WAMO-Desktop starten: Hauptfenster, Stimmen-Rand ueber allen Monitoren, Tray-Symbol.
rem Startet Voice-Core und Oberflaeche selbst, falls sie noch nicht laufen.
rem VS Code setzt ELECTRON_RUN_AS_NODE=1 in seinen Terminals; dann wuerde Electron nur als Node laufen.
set "ELECTRON_RUN_AS_NODE="
set "APP=%~dp0..\desktop"
if not exist "%APP%\node_modules\electron\install.js" (
  echo Electron fehlt. Bitte einmal im Ordner desktop\ "npm install" ausfuehren.
  exit /b 1
)
rem Ab Electron 44 laedt npm install das Programm nicht mehr selbst, sondern erst beim ersten Start.
if not exist "%APP%\node_modules\electron\dist\electron.exe" (
  echo Lade Electron einmalig herunter ...
  node "%APP%\node_modules\electron\install.js"
  if errorlevel 1 (
    echo Electron konnte nicht geladen werden.
    exit /b 1
  )
)
start "" "%APP%\node_modules\electron\dist\electron.exe" "%APP%"
