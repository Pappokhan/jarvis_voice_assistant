# Make Jarvis an app: an icon on the Desktop and in the Start menu that opens the Jarvis window.
#   powershell -ExecutionPolicy Bypass -File install_app.ps1            (install)
#   powershell -ExecutionPolicy Bypass -File install_app.ps1 -Remove    (uninstall the shortcuts)
param([switch]$Remove)
$links = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Jarvis.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Jarvis.lnk")
)
if ($Remove) {
    $links | ForEach-Object { Remove-Item $_ -ErrorAction SilentlyContinue }
    Write-Host "Jarvis shortcuts removed."
    return
}
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $python (Join-Path $PSScriptRoot "make_icon.py") | Out-Null
foreach ($link in $links) {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
    $s.TargetPath = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
    $s.Arguments = "main.py"
    $s.WorkingDirectory = $PSScriptRoot
    $s.IconLocation = Join-Path $PSScriptRoot "assets\jarvis.ico"
    $s.Description = "Jarvis voice assistant"
    $s.Save()
    Write-Host "Created $link"
}
Write-Host "Done. Open Jarvis from the Desktop icon or by searching 'Jarvis' in the Start menu."
