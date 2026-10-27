!ifndef BLACKWOOD_INSTALLER_SAFETY_INCLUDED
!define BLACKWOOD_INSTALLER_SAFETY_INCLUDED
!define BW_MARKER ".blackwood-ui-mirror-install.ini"
!define BW_PRODUCT_ID "Blackwood.UI.Mirror.9A9810B5-2298-4304-A080-903B98175E62"

!ifndef BW_PAYLOAD_ID
  !error "A generated payload manifest is required before installer-safety.nsh"
!endif

; Functions preserve registers and return their result on the stack.
!macro BW_DEFINE_SAFETY_FUNCTIONS PREFIX
Function ${PREFIX}BW_NormalizePath
  Exch $0
  Push $1
  Push $2
  StrCmp $0 "" normalize_invalid
  StrCpy $1 $0 4
  StrCmp $1 "\\?\" normalize_invalid
  StrCmp $1 "\\.\" normalize_invalid
  ; NSIS GetFullPathName requires existing paths; the Win32 API also handles new directories.
  System::Call 'kernel32::GetFullPathNameW(w r0, i ${NSIS_MAX_STRLEN}, w .r1, p 0)i.r2'
  IntCmp $2 0 normalize_invalid
  IntCmp $2 ${NSIS_MAX_STRLEN} normalize_invalid 0 normalize_invalid
  StrCpy $0 $1
  System::Call 'shlwapi::PathIsRootW(w r0)i.r1'
  StrCmp $1 1 normalize_done
  StrCpy $1 $0 1 -1
  StrCmp $1 "\" 0 normalize_done
  StrCpy $0 $0 -1
  Goto normalize_done
normalize_invalid:
  StrCpy $0 ""
normalize_done:
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

Function ${PREFIX}BW_PathsMatch
  Exch $0
  Exch
  Exch $1
  Push $2
  Push $0
  Call ${PREFIX}BW_NormalizePath
  Pop $0
  Push $1
  Call ${PREFIX}BW_NormalizePath
  Pop $1
  StrCpy $2 0
  StrCmp $0 "" paths_done
  StrCmp $1 "" paths_done
  StrCmp $0 $1 0 paths_done
  StrCpy $2 1
paths_done:
  StrCpy $0 $2
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

Function ${PREFIX}BW_PathIsNotReparse
  Exch $0
  Push $1
  Push $2
  Push $0
  Call ${PREFIX}BW_NormalizePath
  Pop $0
  StrCmp $0 "" path_unsafe
path_check:
  System::Call 'kernel32::GetFileAttributesW(w r0)i.r1 ?e'
  Pop $2
  IntCmp $1 -1 path_missing path_present path_present
path_missing:
  StrCmp $2 2 path_parent
  StrCmp $2 3 path_parent path_unsafe
path_present:
  IntOp $2 $1 & 0x400
  IntCmp $2 0 path_parent path_unsafe path_unsafe
path_parent:
  System::Call 'shlwapi::PathIsRootW(w r0)i.r1'
  StrCmp $1 1 path_safe
  StrLen $1 $0
path_find_separator:
  IntOp $1 $1 - 1
  IntCmp $1 0 path_unsafe path_unsafe
  StrCpy $2 $0 1 $1
  StrCmp $2 "\" path_parent_found path_find_separator
path_parent_found:
  IntOp $1 $1 + 1
  StrCpy $0 $0 $1
  Push $0
  Call ${PREFIX}BW_NormalizePath
  Pop $0
  StrCmp $0 "" path_unsafe path_check
path_safe:
  StrCpy $0 1
  Goto path_done
path_unsafe:
  StrCpy $0 0
path_done:
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

Function ${PREFIX}BW_IsOwnedDirectory
  Exch $0
  Push $1
  Push $2
  StrCpy $2 0
  Push $0
  Call ${PREFIX}BW_NormalizePath
  Pop $0
  StrCmp $0 "" owned_done
  System::Call 'shlwapi::PathIsRootW(w r0)i.r1'
  StrCmp $1 1 owned_done
  Push $0
  Call ${PREFIX}BW_PathIsNotReparse
  Pop $1
  StrCmp $1 1 0 owned_done
  ReadINIStr $1 "$0\${BW_MARKER}" "Ownership" "ProductId"
  StrCmp $1 "${BW_PRODUCT_ID}" 0 owned_done
  ReadINIStr $1 "$0\${BW_MARKER}" "Ownership" "Format"
  StrCmp $1 "1" 0 owned_done
  ReadINIStr $1 "$0\${BW_MARKER}" "Ownership" "InstallPath"
  Push $0
  Push $1
  Call ${PREFIX}BW_PathsMatch
  Pop $1
  StrCmp $1 1 0 owned_done
  StrCpy $2 1
owned_done:
  StrCpy $0 $2
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

Function ${PREFIX}BW_IsCurrentPayloadOwner
  Exch $0
  Push $1
  Push $2
  StrCpy $2 0
  Push $0
  Call ${PREFIX}BW_IsOwnedDirectory
  Pop $1
  StrCmp $1 1 0 payload_done
  ReadINIStr $1 "$0\${BW_MARKER}" "Ownership" "PayloadId"
  StrCmp $1 "${BW_PAYLOAD_ID}" 0 payload_done
  StrCpy $2 1
payload_done:
  StrCpy $0 $2
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

Function ${PREFIX}BW_IsInstallDirectorySafe
  Exch $0
  Push $1
  Push $2
  Push $3
  StrCpy $3 0
  Push $0
  Call ${PREFIX}BW_NormalizePath
  Pop $0
  StrCmp $0 "" directory_done
  System::Call 'shlwapi::PathIsRootW(w r0)i.r1'
  StrCmp $1 1 directory_done
  System::Call 'kernel32::GetFileAttributesW(w r0)i.r1 ?e'
  Pop $2
  IntCmp $1 -1 directory_missing directory_present directory_present
directory_missing:
  StrCmp $2 2 directory_check_ancestors
  StrCmp $2 3 directory_check_ancestors directory_done
directory_check_ancestors:
  Push $0
  Call ${PREFIX}BW_PathIsNotReparse
  Pop $2
  StrCmp $2 1 directory_safe directory_done
directory_present:
  IntOp $2 $1 & 0x10
  IntCmp $2 0 directory_done
  Push $0
  Call ${PREFIX}BW_PathIsNotReparse
  Pop $2
  StrCmp $2 1 0 directory_done
  Push $0
  ; Overlaying a different payload leaves its retired files behind, while
  ; replacing the marker would make those files unknown to the new uninstaller.
  ; Only a repair of the exact current payload may reuse a nonempty directory.
  Call ${PREFIX}BW_IsCurrentPayloadOwner
  Pop $2
  StrCmp $2 1 directory_safe
  ClearErrors
  FindFirst $1 $2 "$0\*"
  IfErrors directory_done
directory_loop:
  StrCmp $2 "" directory_empty
  StrCmp $2 "." directory_next
  StrCmp $2 ".." directory_next
  FindClose $1
  Goto directory_done
directory_next:
  FindNext $1 $2
  IfErrors directory_empty
  Goto directory_loop
directory_empty:
  FindClose $1
directory_safe:
  StrCpy $3 1
directory_done:
  StrCpy $0 $3
  Pop $3
  Pop $2
  Pop $1
  Exch $0
FunctionEnd
!macroend
!endif
