Unicode True
!include "MUI2.nsh"
!include "x64.nsh"
Name "Gekko Sync"
OutFile "..\dist\GekkoSync-Setup-0.1.0-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\GekkoSync"
InstallDirRegKey HKCU "Software\GekkoSyncInstaller" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
Icon "..\build\gekko.ico"
UninstallIcon "..\build\gekko.ico"
VIProductVersion "0.1.0.0"
VIAddVersionKey "ProductName" "Gekko Sync"
VIAddVersionKey "FileDescription" "Instalador de Gekko Sync"
VIAddVersionKey "FileVersion" "0.1.0"
VIAddVersionKey "LegalCopyright" "Gekko"
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\GekkoSync.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Abrir Gekko Sync"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Spanish"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "Se requiere Windows de 64 bits."
    Abort
  ${EndIf}
  SetShellVarContext current
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\GekkoSync"
check_running:
  FindWindow $0 "" "Gekko Sync"
  StrCmp $0 0 ready
  MessageBox MB_RETRYCANCEL|MB_ICONINFORMATION "Usa Salir en Gekko Sync, incluido su icono junto al reloj, y vuelve a intentar." IDRETRY check_running
  Abort
ready:
FunctionEnd

Function un.onInit
  SetShellVarContext current
  StrCmp $INSTDIR "$LOCALAPPDATA\Programs\GekkoSync" valid_path
    MessageBox MB_OK|MB_ICONSTOP "La ruta de instalación no coincide. No se eliminará ningún archivo."
    Abort
valid_path:
  FindWindow $0 "" "Gekko Sync"
  StrCmp $0 0 ready
    MessageBox MB_OK|MB_ICONSTOP "Usa Salir en Gekko Sync antes de desinstalar."
    Abort
ready:
FunctionEnd

Section "Gekko Sync"
  SetOutPath "$INSTDIR"
  File /r "..\dist\GekkoSync\*.*"
  WriteUninstaller "$INSTDIR\Desinstalar.exe"
  CreateDirectory "$SMPROGRAMS\Gekko Sync"
  CreateShortcut "$SMPROGRAMS\Gekko Sync\Gekko Sync.lnk" "$INSTDIR\GekkoSync.exe"
  CreateShortcut "$DESKTOP\Gekko Sync.lnk" "$INSTDIR\GekkoSync.exe"
  WriteRegStr HKCU "Software\GekkoSyncInstaller" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GekkoSync" "DisplayName" "Gekko Sync"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GekkoSync" "DisplayVersion" "0.1.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GekkoSync" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GekkoSync" "DisplayIcon" "$INSTDIR\GekkoSync.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GekkoSync" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GekkoSync" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "GekkoSync"
  StrCmp $0 '"$INSTDIR\GekkoSync.exe" --background' remove_startup
  StrCmp $0 '$INSTDIR\GekkoSync.exe --background' remove_startup keep_startup
remove_startup:
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "GekkoSync"
keep_startup:
  Delete "$DESKTOP\Gekko Sync.lnk"
  Delete "$SMPROGRAMS\Gekko Sync\Gekko Sync.lnk"
  RMDir "$SMPROGRAMS\Gekko Sync"
  Delete "$INSTDIR\GekkoSync.exe"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\Desinstalar.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GekkoSync"
  DeleteRegKey HKCU "Software\GekkoSyncInstaller"
SectionEnd
