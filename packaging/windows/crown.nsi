Unicode true
RequestExecutionLevel user
CRCCheck force
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "x64.nsh"
!include "${CROWN_GENERATED}"

!define CROWN_PARENT "$LOCALAPPDATA\Programs\Crown"
!define CROWN_DIRECTORY "${CROWN_PARENT}\${CROWN_VERSION}"
!define CROWN_SHORTCUT "$SMPROGRAMS\Crown ${CROWN_VERSION}.lnk"
!define CROWN_REGISTRY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Crown.${CROWN_VERSION}"

Name "Crown ${CROWN_VERSION}"
OutFile "${CROWN_OUTPUT}"
InstallDir "${CROWN_DIRECTORY}"
BrandingText "Crown"
ShowInstDetails show
ShowUninstDetails show

!define MUI_WELCOMEPAGE_TITLE "Установка Crown"
!define MUI_WELCOMEPAGE_TEXT "Приложение установится для текущего пользователя. Для этой версии будет создан отдельный каталог в профиле Windows."
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

Var Stage

!macro Stop CODE MESSAGE
  MessageBox MB_OK|MB_ICONSTOP "${MESSAGE}" /SD IDOK
  SetErrorLevel ${CODE}
  Quit
!macroend

; Отсутствующая папка допустима. Существующая точка повторной обработки
; или файл вместо папки запрещают запись и удаление через этот путь.
!macro CheckDirectory DIRECTORY FAILURE
  System::Call 'kernel32::GetFileAttributesW(w "${DIRECTORY}") i.r0 ?e'
  Pop $2
  ${If} $0 == -1
    ${If} $2 != 2
    ${AndIf} $2 != 3
      Goto ${FAILURE}
    ${EndIf}
  ${Else}
    IntOp $0 $0 & 0x410
    ${If} $0 != 0x10
      Goto ${FAILURE}
    ${EndIf}
  ${EndIf}
!macroend

!macro CheckParentDirectories FAILURE
  !insertmacro CheckDirectory "$LOCALAPPDATA" ${FAILURE}
  !insertmacro CheckDirectory "$LOCALAPPDATA\Programs" ${FAILURE}
  !insertmacro CheckDirectory "${CROWN_PARENT}" ${FAILURE}
!macroend

!macro RemoveRegistration
  Delete "${CROWN_SHORTCUT}"
  DeleteRegValue HKCU "${CROWN_REGISTRY}" "DisplayName"
  DeleteRegValue HKCU "${CROWN_REGISTRY}" "DisplayVersion"
  DeleteRegValue HKCU "${CROWN_REGISTRY}" "InstallLocation"
  DeleteRegValue HKCU "${CROWN_REGISTRY}" "DisplayIcon"
  DeleteRegValue HKCU "${CROWN_REGISTRY}" "UninstallString"
  DeleteRegValue HKCU "${CROWN_REGISTRY}" "NoModify"
  DeleteRegValue HKCU "${CROWN_REGISTRY}" "NoRepair"
  DeleteRegKey /ifempty HKCU "${CROWN_REGISTRY}"
!macroend

!macro RemoveMetadata ROOT
  Delete "${ROOT}\app-payload.json"
  Delete "${ROOT}\NSIS-license.txt"
  Delete "${ROOT}\install-info.ini"
  Delete "${ROOT}\Uninstall.exe"
  RMDir "${ROOT}"
!macroend

Function .onInit
  SetShellVarContext current
  SetRegView 64
  StrCpy $Stage ""
  ${IfNot} ${IsNativeAMD64}
    !insertmacro Stop 2 "Эта поставка предназначена для Windows x64."
  ${EndIf}
  StrCmp $INSTDIR "${CROWN_DIRECTORY}" 0 refuse
  !insertmacro CheckParentDirectories refuse
  IfFileExists "$INSTDIR" collision 0
  IfFileExists "${CROWN_SHORTCUT}" collision 0
  EnumRegValue $0 HKCU "${CROWN_REGISTRY}" 0
  StrCmp $0 "" 0 collision
  EnumRegKey $0 HKCU "${CROWN_REGISTRY}" 0
  StrCmp $0 "" 0 collision
  Return

  collision:
    !insertmacro Stop 3 "Место для этой версии уже занято. Используйте установленную версию или сначала удалите её."
  refuse:
    !insertmacro Stop 2 "Для установки нужен обычный каталог этой версии в профиле Windows."
FunctionEnd

Function RemoveStage
  StrCmp $Stage "" done
  !insertmacro CheckDirectory "$Stage" done
  !insertmacro CheckDirectory "$Stage\App" done
  !insertmacro CheckPayloadDirectories "$Stage\App" done
  SetOutPath "$TEMP"
  !insertmacro RemovePayload "$Stage\App"
  !insertmacro PrunePayload "$Stage\App"
  RMDir "$Stage\App"
  !insertmacro RemoveMetadata "$Stage"
  done:
FunctionEnd

Function .onInstFailed
  Call RemoveStage
FunctionEnd

Section "Crown"
  ClearErrors
  CreateDirectory "${CROWN_PARENT}"
  IfErrors failed
  !insertmacro CheckParentDirectories failed
  GetTempFileName $1 "${CROWN_PARENT}"
  IfErrors failed
  Delete "$1"
  IfErrors failed
  ; Сохраняем имя для очистки только после успешного создания новой папки.
  System::Call 'kernel32::CreateDirectoryW(w r1, p 0) i.r0'
  StrCmp $0 1 0 failed
  StrCpy $Stage $1

  ClearErrors
  !insertmacro InstallPayload "$Stage\App"
  IfErrors failed
  SetOutPath "$Stage"
  File "/oname=app-payload.json" "${CROWN_MANIFEST}"
  File "/oname=NSIS-license.txt" "${CROWN_NSIS_LICENSE}"
  WriteINIStr "$Stage\install-info.ini" "Crown" "Version" "${CROWN_VERSION}"
  WriteINIStr "$Stage\install-info.ini" "Crown" "Payload" "${CROWN_PAYLOAD_SHA}"
  WriteUninstaller "$Stage\Uninstall.exe"
  IfErrors failed
  SetOutPath "${CROWN_PARENT}"
  Rename "$Stage" "$INSTDIR"
  IfErrors failed
  StrCpy $Stage ""

  SetOutPath "$INSTDIR\App"
  CreateShortCut "${CROWN_SHORTCUT}" "$INSTDIR\App\PersonalFinance.exe"
  IfErrors failed_registration
  WriteRegStr HKCU "${CROWN_REGISTRY}" "DisplayName" "Crown ${CROWN_VERSION}"
  WriteRegStr HKCU "${CROWN_REGISTRY}" "DisplayVersion" "${CROWN_VERSION}"
  WriteRegStr HKCU "${CROWN_REGISTRY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${CROWN_REGISTRY}" "DisplayIcon" "$INSTDIR\App\PersonalFinance.exe"
  WriteRegStr HKCU "${CROWN_REGISTRY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegDWORD HKCU "${CROWN_REGISTRY}" "NoModify" 1
  WriteRegDWORD HKCU "${CROWN_REGISTRY}" "NoRepair" 1
  IfErrors failed_registration
  Goto done

  failed_registration:
    !insertmacro RemoveRegistration
    StrCpy $Stage "$INSTDIR"
  failed:
    Call RemoveStage
    !insertmacro Stop 4 "Установка не завершилась. Проверьте место и доступ к каталогу профиля Windows."
  done:
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  StrCmp $INSTDIR "${CROWN_DIRECTORY}" 0 refuse
  !insertmacro CheckParentDirectories refuse
  !insertmacro CheckDirectory "$INSTDIR" refuse
  !insertmacro CheckDirectory "$INSTDIR\App" refuse
  !insertmacro CheckPayloadDirectories "$INSTDIR\App" refuse
  ReadINIStr $0 "$INSTDIR\install-info.ini" "Crown" "Version"
  StrCmpS $0 "${CROWN_VERSION}" 0 refuse
  ReadINIStr $0 "$INSTDIR\install-info.ini" "Crown" "Payload"
  StrCmpS $0 "${CROWN_PAYLOAD_SHA}" 0 refuse
  Return

  refuse:
    !insertmacro Stop 2 "Папка установки изменилась. Удаление остановлено."
FunctionEnd

Section "Uninstall"
  SetOutPath "$TEMP"
  ClearErrors
  ; Сначала проверяем удаление главного EXE. Загруженный Windows файл
  ; остаётся на месте, а остальные файлы в этом случае не трогаются.
  Delete "$INSTDIR\App\PersonalFinance.exe"
  IfErrors busy
  !insertmacro RemovePayload "$INSTDIR\App"
  IfErrors failed
  !insertmacro PrunePayload "$INSTDIR\App"
  RMDir "$INSTDIR\App"
  !insertmacro RemoveRegistration
  !insertmacro RemoveMetadata "$INSTDIR"
  Goto done

  busy:
    !insertmacro Stop 3 "Закройте Crown и повторите удаление."
  failed:
    !insertmacro Stop 4 "Часть файлов занята. Закройте другие приложения и повторите удаление."
  done:
SectionEnd

