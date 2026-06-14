# Run Index Options AI at Windows logon (laptop must stay powered on).
# Right-click PowerShell → Run as Administrator:
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   .\scripts\register-autostart.ps1

$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TaskName = "IndexOptionsAI"
$Cmd = Join-Path $Repo "Start Index Options AI.cmd"

$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$Cmd`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Start Index Options AI server at logon" -Force
Write-Host "Registered scheduled task: $TaskName"
Write-Host "For 24/7 trading when your laptop is OFF, use a cloud VPS — see deploy/index-options-ai.service"
