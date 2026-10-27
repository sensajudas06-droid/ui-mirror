Unicode True
!include "MUI2.nsh"

!ifndef PROJECT_DIR
  !error "PROJECT_DIR must be passed to makensis"
!endif
!ifndef BUILD_DIR
  !error "BUILD_DIR must be passed to makensis"
!endif
!ifndef OUTPUT_DIR
  !error "OUTPUT_DIR must be passed to makensis"
!endif
!ifndef PAYLOAD_MANIFEST
  !error "PAYLOAD_MANIFEST must be passed to makensis"
!endif
!include "${PAYLOAD_MANIFEST}"
!include "${PROJECT_DIR}\packaging\installer-safety.nsh"

!define APP_NAME "Blackwood UI Mirror"
!define APP_VERSION "1.9.11"
!define APP_EXE "Blackwood UI Mirror.exe"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\BlackwoodUIMirror"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${OUTPUT_DIR}\Blackwood-UIMirror-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "1.9.11.0"
VIAddVersionKey /LANG=1055 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=1055 "FileDescription" "${APP_NAME} kurulum"
VIAddVersionKey /LANG=1055 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1055 "CompanyName" "Blackwood"
VIAddVersionKey /LANG=1055 "LegalCopyright" "Copyright (c) Blackwood"

!define MUI_ICON "${PROJECT_DIR}\assets\blackwood_mark_generated.ico"
!define MUI_UNICON "${PROJECT_DIR}\assets\blackwood_mark_generated.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE BW_DirectoryPageLeave
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Turkish"

!insertmacro BW_DEFINE_SAFETY_FUNCTIONS ""
!insertmacro BW_DEFINE_SAFETY_FUNCTIONS "un."

Function .onInit
  SetShellVarContext current
FunctionEnd

Function BW_ValidateSelectedDirectory
  Push "$INSTDIR"
  Call BW_NormalizePath
  Pop $INSTDIR
  Push "$INSTDIR"
  Call BW_IsInstallDirectorySafe
  Pop $0
  StrCmp $0 1 0 directory_rejected
  !insertmacro BW_CHECK_PAYLOAD_PATHS "" $0
  StrCmp $0 1 0 directory_rejected
  ClearErrors
  Return
directory_rejected:
  SetErrors
FunctionEnd

Function BW_DirectoryPageLeave
  Call BW_ValidateSelectedDirectory
  IfErrors 0 directory_page_done
  MessageBox MB_OK|MB_ICONSTOP "Bu klasör kurulum için uygun değil. Yeni veya boş bir klasör seçin.$\r$\n$\r$\nFarklı bir kurulumun üzerine yazılmaz. Mevcut kurulumu onarmak için aynı kurulum dosyasını kullanın." /SD IDOK
  Abort
directory_page_done:
FunctionEnd

Function un.onInit
  SetShellVarContext current
  Push "$INSTDIR"
  Call un.BW_NormalizePath
  Pop $INSTDIR
  Push "$INSTDIR"
  Call un.BW_IsCurrentPayloadOwner
  Pop $0
  StrCmp $0 1 0 uninstaller_rejected
  !insertmacro BW_CHECK_PAYLOAD_PATHS "un." $0
  StrCmp $0 1 0 uninstaller_rejected
  Return
uninstaller_rejected:
  MessageBox MB_OK|MB_ICONSTOP "Kaldırıcı bu klasörün kurulum kaydıyla eşleşmiyor. Kurulumun kendi klasöründeki güncel kaldırıcıyı kullanın." /SD IDOK
  Abort
FunctionEnd

Section "Uygulama" SEC_MAIN
  SectionIn RO
  Call BW_ValidateSelectedDirectory
  IfErrors 0 install_directory_ready
  MessageBox MB_OK|MB_ICONSTOP "Kurulum klasörü uygun değil. Yeni veya boş bir klasör seçin. Önceki kurulum otomatik taşınmaz." /SD IDOK
  Abort
install_directory_ready:
  ClearErrors
  SetOutPath "$INSTDIR"
  IfErrors install_failed
  File /r "${BUILD_DIR}\*.*"
  IfErrors install_failed
  WriteINIStr "$INSTDIR\${BW_MARKER}" "Ownership" "ProductId" "${BW_PRODUCT_ID}"
  WriteINIStr "$INSTDIR\${BW_MARKER}" "Ownership" "Format" "1"
  WriteINIStr "$INSTDIR\${BW_MARKER}" "Ownership" "InstallPath" "$INSTDIR"
  WriteINIStr "$INSTDIR\${BW_MARKER}" "Ownership" "PayloadId" "${BW_PAYLOAD_ID}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  IfErrors install_failed

  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\Kaldır.lnk" "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Blackwood"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  Goto install_complete
install_failed:
  MessageBox MB_OK|MB_ICONSTOP "Kurulum dosyaları yazılamadı. Çalışan UI Mirror pencerelerini kapatıp kurulumu yeniden deneyin." /SD IDOK
  Abort
install_complete:
SectionEnd

Section /o "Masaüstü kısayolu" SEC_DESKTOP
  ClearErrors
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  IfErrors desktop_shortcut_done
  WriteINIStr "$INSTDIR\${BW_MARKER}" "Ownership" "DesktopShortcut" "1"
desktop_shortcut_done:
SectionEnd

Section "Uninstall"
  ClearErrors
  !insertmacro BW_DELETE_PAYLOAD_FILES
  IfErrors uninstall_incomplete
  !insertmacro BW_REMOVE_PAYLOAD_DIRS

  ; A previous installation must not unregister a later installation elsewhere.
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  Push "$INSTDIR"
  Push "$0"
  Call un.BW_PathsMatch
  Pop $0
  StrCmp $0 1 0 uninstall_local_cleanup
  ReadINIStr $0 "$INSTDIR\${BW_MARKER}" "Ownership" "DesktopShortcut"
  StrCmp $0 1 0 uninstall_startmenu_cleanup
  Delete "$DESKTOP\${APP_NAME}.lnk"
uninstall_startmenu_cleanup:
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Kaldır.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
uninstall_local_cleanup:
  ClearErrors
  Delete "$INSTDIR\${BW_MARKER}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Goto uninstall_complete
uninstall_incomplete:
  MessageBox MB_OK|MB_ICONSTOP "Bazı uygulama dosyaları kaldırılamadı. UI Mirror pencerelerini kapatıp kaldırıcıyı yeniden çalıştırın." /SD IDOK
  Abort
uninstall_complete:
SectionEnd
