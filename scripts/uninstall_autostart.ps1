# Removes only the TaskCoordinator scheduled task. Database, backups, and logs stay in place.
$ErrorActionPreference = "Stop"
schtasks /Delete /TN "TaskCoordinator" /F
Write-Host "Scheduled task removed. Data was not deleted."
