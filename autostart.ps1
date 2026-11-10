# Start Jarvis in the background every time you sign in to Windows.
#   powershell -ExecutionPolicy Bypass -File autostart.ps1            (enable)
#   powershell -ExecutionPolicy Bypass -File autostart.ps1 -Remove    (disable)
param([switch]$Remove)
$link = Join-Path ([Environment]::GetFolderPath("Startup")) "Jarvis.lnk"
if ($Remove) {
    Remove-Item $link -ErrorAction SilentlyContinue
    Write-Host "Jarvis will no longer start with Windows."
    return
}
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$s.TargetPath = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
$s.Arguments = "main.py --hidden"   # no window at sign-in; open it from the tray icon or the Jarvis shortcut
$icon = Join-Path $PSScriptRoot "assets\jarvis.ico"; if (Test-Path $icon) { $s.IconLocation = $icon }
$s.WorkingDirectory = $PSScriptRoot
$s.Description = "Jarvis voice assistant"
$s.Save()
Write-Host "Jarvis will start in the background when you sign in. ($link)"

