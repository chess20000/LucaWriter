!macro customInit
!macroend

!macro customInstall
  WriteRegStr HKCU "Software\LucaWriter" "InstallPath" "$INSTDIR"
!macroend

!macro customUnInstall
  MessageBox MB_YESNO|MB_ICONQUESTION "Do you want to completely remove all user data? $\n$\nThis will delete:$\n- All books and chapters$\n- User accounts and settings$\n- Downloaded AI models$\n- Application cache and logs$\n$\nClick YES to remove everything.$\nClick NO to keep user data." IDYES removeAll IDNO keepData

  removeAll:
    RMDir /r "$LOCALAPPDATA\LucaWriter"
    RMDir /r "$APPDATA\LucaWriter"
    RMDir /r "$INSTDIR\..\local_llm"
    RMDir /r "$TEMP\LucaWriter"
    DeleteRegKey HKCU "Software\LucaWriter"
    Goto unInstallEnd

  keepData:
    Goto unInstallEnd

  unInstallEnd:
!macroend
