#install_paper.ps1 — 安装/卸载全局 paper 命令（uv tool editable）
<#
.SYNOPSIS
    把 paper / paper-research 安装为全局命令（uv tool --editable），并确保工具目录在用户 PATH。

.DESCRIPTION
    editable 安装不会复制源码，src/paths.py 的 ROOT_DIR 仍指向项目根，config/data/log 位置不变；
    之后修改 src/ 代码即时生效，无需重装（新增 entry point 除外）。

    脚本做三件事：
      1. 在项目根运行 `uv tool install --editable . --force`（生成 paper.exe / paper-research.exe）；
      2. 确保 %USERPROFILE%\.local\bin（uv tool bin 目录）在用户 PATH 中（.NET API，无需管理员）；
      3. 自检 paper --help 输出。

.PARAMETER Uninstall
    卸载工具（uv tool uninstall paper-research）。默认不移除 PATH 项——.local\bin 是 uv 的标准
    工具目录，可能被其他 uv tool 共用；配合 -RemovePath 才从用户 PATH 移除。

.PARAMETER RemovePath
    与 -Uninstall 搭配：同时把 %USERPROFILE%\.local\bin 从用户 PATH 移除。

.PARAMETER SkipPathUpdate
    只安装工具，不改用户 PATH。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install_paper.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\install_paper.ps1 -Uninstall
#>
param(
    [switch]$Uninstall,
    [switch]$RemovePath,
    [switch]$SkipPathUpdate
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$ToolBin = Join-Path $env:USERPROFILE '.local\bin'
$ToolName = 'paper-research'

function Update-UserPath {
    param([string]$Entry, [switch]$Remove)
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($userPath.Split(';') | Where-Object { $_ -ne '' })
    $norm = $Entry.TrimEnd('\')
    $hit = @($parts | Where-Object { $_.TrimEnd('\') -ieq $norm })
    if ($Remove) {
        if ($hit.Count -gt 0) {
            $newParts = @($parts | Where-Object { $_.TrimEnd('\') -ine $norm })
            [Environment]::SetEnvironmentVariable('Path', ($newParts -join ';'), 'User')
            Write-Host "[OK] 已从用户 PATH 移除 $Entry（新终端生效）"
        } else {
            Write-Host "[OK] $Entry 不在用户 PATH，无需移除"
        }
    } else {
        if ($hit.Count -gt 0) {
            Write-Host "[OK] $Entry 已在用户 PATH"
        } else {
            $newPath = (@($parts) + @($norm)) -join ';'
            [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
            Write-Host "[OK] 已把 $Entry 加入用户 PATH（新终端生效）"
        }
    }
}

# ── 卸载 ───────────────────────────────────────────────────
if ($Uninstall) {
    & uv tool uninstall $ToolName 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "[!] 未找到已安装的工具 $ToolName（可忽略）" }
    if (-not $SkipPathUpdate) {
        if ($RemovePath) {
            Update-UserPath -Entry $ToolBin -Remove
        } else {
            Write-Host "[i] 未移除用户 PATH 中的 $ToolBin（.local\bin 可能被其他 uv tool 共用；"
            Write-Host "    如需移除请加 -RemovePath：install_paper.ps1 -Uninstall -RemovePath）"
        }
    }
    Write-Host "[OK] paper 已卸载。"
    exit 0
}

# ── 安装前检查 ─────────────────────────────────────────────
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "未找到 uv，请先安装 uv：https://docs.astral.sh/uv/"
}
if (-not (Test-Path (Join-Path $ProjectRoot '.venv\Scripts\python.exe'))) {
    Write-Warning "未找到 $ProjectRoot\.venv\Scripts\python.exe；仍将尝试 editable 安装（会为 tool 建独立环境）"
}

# ── 1. editable 安装 ───────────────────────────────────────
Push-Location $ProjectRoot
try {
    Write-Host "[*] uv tool install --editable . --force ..."
    & uv tool install --editable . --force
    if ($LASTEXITCODE -ne 0) { throw "uv tool install 失败 (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

# ── 2. 用户 PATH ───────────────────────────────────────────
if (-not $SkipPathUpdate) {
    Update-UserPath -Entry $ToolBin
}

# ── 3. 自检 ────────────────────────────────────────────────
Write-Host "[*] 自检 paper --help ..."
& (Join-Path $ToolBin 'paper.exe') --help | Select-Object -First 1
Write-Host ""
Write-Host "完成。请新开一个终端后使用（Git Bash / cmd / PowerShell 均可）："
Write-Host "    paper serve      # 启动 Web 审阅服务"
Write-Host "    paper fetch      # 抓取论文"
Write-Host "    paper status     # 统计仪表盘"
Write-Host "    paper notify     # 手动发送今日邮件"
Write-Host "    paper autostart  # 注册开机自启（需管理员，非管理员会打印手动命令）"
