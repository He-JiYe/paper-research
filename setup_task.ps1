# Paper Research - Windows Task Scheduler Setup
# 以管理员身份在 PowerShell 中运行此脚本
# 用法:
#   .\setup_task.ps1 -Action register -Time "08:30"
#   .\setup_task.ps1 -Action unregister
#   .\setup_task.ps1 -Action run-now

param(
    [ValidateSet("register", "unregister", "run-now", "status")]
    [string]$Action = "register",
    [string]$Time = "08:30"
)

$TaskName = "PaperResearchDailyFetch"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find Python path (prefer the uv .venv)
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
    Write-Host "Using venv Python: $PythonExe"
} else {
    $PythonExe = (Get-Command python -ErrorAction Stop).Source
    Write-Host "Using system Python: $PythonExe"
}

$ScriptPath = Join-Path $ProjectRoot "src\cli\main.py"
$WorkingDir = $ProjectRoot
$Arguments = "$ScriptPath fetch"

Write-Host "Project Root: $ProjectRoot"
Write-Host "Script Path: $ScriptPath"
Write-Host ""

switch ($Action) {
    "register" {
        # 检查是否已存在
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Host "Task '$TaskName' already exists. Use -Action unregister first to remove it."
            Write-Host "Or use -Action run-now to test it."
            return
        }

        $Action = New-ScheduledTaskAction `
            -Execute $PythonExe `
            -Argument $Arguments `
            -WorkingDirectory $WorkingDir

        $Trigger = New-ScheduledTaskTrigger -Daily -At $Time

        # 设置：不存储密码（仅登录时运行）
        $Principal = New-ScheduledTaskPrincipal `
            -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive `
            -RunLevel Limited

        $Settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -RunOnlyIfNetworkAvailable `
            -MultipleInstances IgnoreNew

        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $Action `
            -Trigger $Trigger `
            -Principal $Principal `
            -Settings $Settings `
            -Description "Daily Arxiv paper fetch and summarization - Paper Research Tool" `
            -Force

        Write-Host ""
        Write-Host "============================================"
        Write-Host "  Task '$TaskName' registered successfully!"
        Write-Host "  Runs daily at: $Time"
        Write-Host "  Python: $PythonExe"
        Write-Host "============================================"
        Write-Host ""
        Write-Host "Tips:"
        Write-Host "  - Set API key: setx DEEPSEEK_API_KEY ""your-key-here"""
        Write-Host "  - Test run:    .\setup_task.ps1 -Action run-now"
        Write-Host "  - View tasks:  taskschd.msc"
        Write-Host "  - Check log:   .\setup_task.ps1 -Action status"
    }

    "unregister" {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Task '$TaskName' unregistered."
    }

    "run-now" {
        Write-Host "Starting task '$TaskName'..."
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Task started. Check results in Task Scheduler or run:"
        Write-Host "  uv run paper-research status"
    }

    "status" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Write-Host "Task: $TaskName"
            Write-Host "State: $($task.State)"
            Write-Host "Next run: $($task.NextRunTime)"
            Write-Host "Last run: $($task.LastRunTime)"
            Write-Host "Last result: $($task.LastTaskResult)"
        } else {
            Write-Host "Task '$TaskName' not found. Register with:"
            Write-Host "  .\setup_task.ps1 -Action register -Time '09:00'"
        }
    }
}
