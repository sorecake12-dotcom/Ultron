@echo off
title ULTRON -- Create Desktop Shortcuts
color 0A

cd /d "%~dp0"

echo =====================================================
echo   ULTRON -- Creating Desktop Shortcuts...
echo =====================================================
echo.

set "TARGET_DIR=%~dp0"

powershell -Command ^
  "$targets = @('%USERPROFILE%\Desktop', '%USERPROFILE%\OneDrive\Desktop'); " ^
  "$ico = '%TARGET_DIR%config\ultron.ico'; " ^
  "foreach ($d in $targets) { " ^
  "  if (Test-Path $d) { " ^
  "    $ws = New-Object -ComObject WScript.Shell; " ^
  "    $s1 = $ws.CreateShortcut(\"$d\ULTRON OS.lnk\"); $s1.TargetPath = '%TARGET_DIR%START_ALL.bat'; $s1.WorkingDirectory = '%TARGET_DIR%'; $s1.IconLocation = $ico; $s1.Description = 'Launch ULTRON Next-Gen AI OS'; $s1.Save(); " ^
  "    $s2 = $ws.CreateShortcut(\"$d\ULTRON Full System.lnk\"); $s2.TargetPath = '%TARGET_DIR%START_ALL.bat'; $s2.WorkingDirectory = '%TARGET_DIR%'; $s2.IconLocation = $ico; $s2.Description = 'Launch ULTRON Main AI + Wake Word Listener'; $s2.Save(); " ^
  "    $s3 = $ws.CreateShortcut(\"$d\ULTRON AI.lnk\"); $s3.TargetPath = '%TARGET_DIR%START_ULTRON.bat'; $s3.WorkingDirectory = '%TARGET_DIR%'; $s3.IconLocation = $ico; $s3.Description = 'Launch ULTRON AI Assistant'; $s3.Save(); " ^
  "    $s4 = $ws.CreateShortcut(\"$d\ULTRON Wake Word.lnk\"); $s4.TargetPath = '%TARGET_DIR%Start_ULTRON_Wake_Word.bat'; $s4.WorkingDirectory = '%TARGET_DIR%'; $s4.IconLocation = $ico; $s4.Description = 'Start ULTRON Wake Word Listener'; $s4.Save(); " ^
  "    Write-Host \"[OK] Created custom icon shortcuts in: $d\"; " ^
  "  } " ^
  "}"

echo.
echo =====================================================
echo   Shortcuts successfully created on Desktop!
echo   Target Project Directory: %TARGET_DIR%
echo =====================================================
echo.
