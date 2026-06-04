!macro customInit
  # 备份用户数据（local_llm/models 和 usrdata），防止更新时被旧版卸载器删除
  ${If} ${FileExists} "$INSTDIR\resources\local_llm\models\*.*"
    CreateDirectory "$TEMP\LucaWriter\ModelsBackup"
    CopyFiles /SILENT "$INSTDIR\resources\local_llm\models\*.*" "$TEMP\LucaWriter\ModelsBackup"
  ${EndIf}
  ${If} ${FileExists} "$INSTDIR\usrdata\*.*"
    CreateDirectory "$TEMP\LucaWriter\UsrdataBackup"
    CopyFiles /SILENT "$INSTDIR\usrdata\*.*" "$TEMP\LucaWriter\UsrdataBackup"
  ${EndIf}
  # 也备份旧安装路径下的 local_llm（如果用户换了安装目录）
  ReadRegStr $0 HKCU "Software\LucaWriter" "InstallPath"
  StrCmp $0 "" skipOldInstall
  StrCmp $0 "$INSTDIR" skipOldInstall
  ${If} ${FileExists} "$0\resources\local_llm\models\*.*"
    CreateDirectory "$TEMP\LucaWriter\ModelsBackup"
    CopyFiles /SILENT "$0\resources\local_llm\models\*.*" "$TEMP\LucaWriter\ModelsBackup"
  ${EndIf}
  skipOldInstall:
!macroend

!macro customInstall
  WriteRegStr HKCU "Software\LucaWriter" "InstallPath" "$INSTDIR"
  # 恢复备份的模型文件
  ${If} ${FileExists} "$TEMP\LucaWriter\ModelsBackup\*.*"
    CreateDirectory "$INSTDIR\resources\local_llm\models"
    CopyFiles /SILENT "$TEMP\LucaWriter\ModelsBackup\*.*" "$INSTDIR\resources\local_llm\models"
    RMDir /r "$TEMP\LucaWriter\ModelsBackup"
  ${EndIf}
  # 恢复备份的 usrdata
  ${If} ${FileExists} "$TEMP\LucaWriter\UsrdataBackup\*.*"
    CreateDirectory "$INSTDIR\usrdata"
    CopyFiles /SILENT "$TEMP\LucaWriter\UsrdataBackup\*.*" "$INSTDIR\usrdata"
    RMDir /r "$TEMP\LucaWriter\UsrdataBackup"
  ${EndIf}
!macroend

!macro customUnInstall
  ${If} ${Silent}
    Goto keepData
  ${EndIf}
  MessageBox MB_YESNO|MB_ICONQUESTION "Do you want to completely remove all user data? $\n$\nThis will delete:$\n- All books and chapters$\n- User accounts and settings$\n- Downloaded AI models$\n- Application cache and logs$\n$\nClick YES to remove everything.$\nClick NO to keep user data." IDYES removeAll IDNO keepData

  removeAll:
    RMDir /r "$LOCALAPPDATA\LucaWriter"
    RMDir /r "$APPDATA\LucaWriter"
    RMDir /r "$TEMP\LucaWriter"
    DeleteRegKey HKCU "Software\LucaWriter"
    Goto unInstallEnd

  keepData:
    Goto unInstallEnd

  unInstallEnd:
!macroend
