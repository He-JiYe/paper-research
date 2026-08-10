#register_task.ps1 — Windows 计划任务注册脚本（无控制台弹窗启动）
<#
.SYNOPSIS
    注册/卸载 Paper Research 的 Windows 计划任务（用 base pythonw.exe 跑 serve_headless.py，无黑窗）。

.DESCRIPTION
    绕过 uv venv 的 pythonw 重定向器（会再拉起一个 CUI 子进程产生黑窗），
    直接以 base 解释器的 pythonw.exe 运行 serve_headless.py（内部手动加 venv site-packages）。

.PARAMETER Mode
    startup : 开机登录后自动后台启动 serve（内置调度器每天定时抓取）——推荐
    daily   : 每天指定时间执行一次 fetch（不常驻 serve），配合 -Time

.PARAMETER Action
    status / run-now / unregister

.PARAMETER Time
    daily 模式的执行时间，如 "08:30"

.EXAMPLE
    .\scripts\register_task.ps1 -Mode startup
    .\scripts\register_task.ps1 -Mode daily -Time "08:30"
    .\scripts\register_task.ps1 -Action status
#>
param(
    [ValidateSet("startup", "daily")] [string]$Mode,
    [ValidateSet("status", "run-now", "unregister")] [string]$Action,
    [string]$Time = "08:30"
)

$ErrorActionPreference = "Stop"
# 脚本位于 <项目根>\scripts\，向上算一层即项目根
$ProjectRoot = Split-Path $PSScriptRoot -Parent

# ── 定位 base pythonw（从 .venv/pyvenv.cfg 的 home 读取）──────────
$PyVenvCfg = Join-Path $ProjectRoot ".venv\pyvenv.cfg"
if (-not (Test-Path $PyVenvCfg)) {
    Write-Error "未找到 $PyVenvCfg，请先 uv sync 创建虚拟环境"
}
$PyHome = Select-String -Path $PyVenvCfg -Pattern '^\s*home\s*=\s*(.+)$' | ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() }
$BasePythonw = Join-Path $PyHome "pythonw.exe"
if (-not (Test-Path $BasePythonw)) {
    Write-Error "未找到 base pythonw: $BasePythonw"
}

$Headless = Join-Path $ProjectRoot "serve_headless.py"
$WorkDir = $ProjectRoot

$TaskNameStartup = "PaperResearchServe"
$TaskNameDaily = "PaperResearchFetch"

function Register-StartupTask {
    $action = New-ScheduledTaskAction -Execute $BasePythonw -Argument "`"$Headless`" serve" -WorkingDirectory $WorkDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskNameStartup -Action $action -Trigger $trigger -Settings $settings -Description "Paper Research serve（后台，无窗口）" -Force
    Write-Host "[OK] 已注册开机自启任务: $TaskNameStartup"
}

function Register-DailyTask {
    $action = New-ScheduledTaskAction -Execute $BasePythonw -Argument "`"$Headless`" fetch" -WorkingDirectory $WorkDir
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskNameDaily -Action $action -Trigger $trigger -Settings $settings -Description "Paper Research 每日抓取" -Force
    Write-Host "[OK] 已注册每日抓取任务: $TaskNameDaily @ $Time"
}

switch ($Action) {
    "status" {
        Get-ScheduledTask -TaskName $TaskNameStartup, $TaskNameDaily -ErrorAction SilentlyContinue | Format-Table TaskName, State
        break
    }
    "run-now" {
        Start-ScheduledTask -TaskName $TaskNameStartup -ErrorAction SilentlyContinue
        Write-Host "[OK] 已触发 $TaskNameStartup"
        break
    }
    "unregister" {
        Unregister-ScheduledTask -TaskName $TaskNameStartup -Confirm:$false -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskNameDaily -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "[OK] 已卸载计划任务"
        break
    }
    default {
        if ($Mode -eq "startup") { Register-StartupTask }
        if ($Mode -eq "daily") { Register-DailyTask }
        if (-not $Mode -and -not $Action) {
            Write-Host "用法: -Mode startup | -Mode daily -Time ""08:30"" | -Action status|run-now|unregister"
        }
    }
}
