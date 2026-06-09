# =============================================================
# Translator InTime - Windows 环境设置脚本
# 安装依赖 + Windows 音频捕获说明
# 支持: Windows 10/11
# 以管理员身份运行 PowerShell
# =============================================================

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Translator InTime - Windows 环境设置" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# ---- 1. 检查 Python ----
Write-Host ""
Write-Host "[1/4] 检查 Python 环境..." -ForegroundColor Yellow

$pythonCmd = $null
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} elseif (Get-Command "python3" -ErrorAction SilentlyContinue) {
    $pythonCmd = "python3"
} else {
    Write-Host "错误: 未找到 Python！请安装 Python 3.10+ https://www.python.org/" -ForegroundColor Red
    exit 1
}

Write-Host "Python 路径: $((Get-Command $pythonCmd).Source)" -ForegroundColor Green

# ---- 2. 创建虚拟环境 ----
Write-Host ""
Write-Host "[2/4] 创建 Python 虚拟环境..." -ForegroundColor Yellow

Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location ..

if (-not (Test-Path "venv")) {
    & $pythonCmd -m venv venv
}

$venvActivate = ".\venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
    Write-Host "虚拟环境已激活" -ForegroundColor Green
}

# 升级 pip 并安装依赖
& pip install --upgrade pip
& pip install -r requirements.txt

# ---- 3. 音频捕获/虚拟音频说明 ----
Write-Host ""
Write-Host "[3/4] 音频捕获/虚拟音频说明..." -ForegroundColor Yellow

Write-Host ""
Write-Host "系统/游戏声音捕获:" -ForegroundColor Cyan
Write-Host "  默认使用 WASAPI loopback，可直接捕获扬声器/耳机输出。" -ForegroundColor Green
Write-Host "  设置中选择 [Loopback] 开头的设备即可。" -ForegroundColor Green
Write-Host ""
Write-Host "如果你还需要把 TTS 输出到游戏麦克风，请手动安装虚拟音频驱动之一:" -ForegroundColor Cyan
Write-Host "  推荐: VB-Cable" -ForegroundColor White
Write-Host "    下载: https://vb-audio.com/Cable/" -ForegroundColor Gray
Write-Host ""
Write-Host "  备选: Voicemeeter (更强大)" -ForegroundColor White
Write-Host "    下载: https://vb-audio.com/Voicemeeter/" -ForegroundColor Gray
Write-Host ""
Write-Host "安装后，系统中会出现以下虚拟设备:" -ForegroundColor Yellow
Write-Host "  - CABLE Input (VB-Audio Virtual Cable)  → TTS输出设备" -ForegroundColor Green
Write-Host "  - CABLE Output (VB-Audio Virtual Cable) → 游戏声音捕获" -ForegroundColor Green

$response = Read-Host "是否需要安装/已安装虚拟音频驱动？仅捕获系统声音可输入 n (y/n)"
if ($response -ne "y") {
    Write-Host "将使用 WASAPI loopback 捕获系统输出。" -ForegroundColor Yellow
}

# ---- 4. 列出音频设备 ----
Write-Host ""
Write-Host "[4/4] 当前音频设备:" -ForegroundColor Yellow
& $pythonCmd -c @"
import sounddevice as sd
devices = sd.query_devices()
for i, d in enumerate(devices):
    in_ch = d['max_input_channels']
    out_ch = d['max_output_channels']
    tag_in = 'IN' if in_ch > 0 else '  '
    tag_out = 'OUT' if out_ch > 0 else '   '
    print(f'  [{i}] {tag_in} {tag_out} {d[\"name\"]}')
"@

# ---- 完成 ----
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " 使用说明" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. 设置 API Key:" -ForegroundColor White
Write-Host '   set OPENAI_API_KEY="sk-..."' -ForegroundColor Gray
Write-Host "   # 或在应用的设置界面中输入" -ForegroundColor Gray
Write-Host ""
Write-Host "2. 启动应用:" -ForegroundColor White
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Gray
Write-Host "   python run.py" -ForegroundColor Gray
Write-Host ""
Write-Host "3. 音频设备配置（设置对话框）:" -ForegroundColor White
Write-Host "   - 麦克风: 你的物理麦克风" -ForegroundColor Green
Write-Host "   - 游戏声音捕获: [Loopback] 你的扬声器/耳机" -ForegroundColor Green
Write-Host "   - TTS输出设备: 系统默认扬声器，或 CABLE Input（要送进游戏麦克风时）" -ForegroundColor Green
Write-Host ""
Write-Host "4. 若使用 VB-Cable 输出到游戏，请在游戏中设置麦克风为 CABLE Input" -ForegroundColor White
Write-Host "============================================" -ForegroundColor Cyan
