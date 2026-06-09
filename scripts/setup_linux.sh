#!/bin/bash
# =============================================================
# Translator InTime - Linux 环境设置脚本
# 安装依赖 + 配置 PipeWire/PulseAudio 虚拟音频设备
# 支持: Ubuntu 22.04+
# =============================================================
set -e

echo "============================================"
echo " Translator InTime - Linux 环境设置"
echo "============================================"

# ---- 1. 安装系统依赖 ----
echo ""
echo "[1/4] 安装系统依赖..."

if [ -f /etc/debian_version ]; then
    sudo apt-get update
    sudo apt-get install -y \
        python3 python3-pip python3-venv \
        portaudio19-dev python3-pyaudio \
        pulseaudio-utils \
        libportaudio2 \
        libasound2-dev \
        ffmpeg
elif [ -f /etc/arch-release ]; then
    sudo pacman -S --noconfirm \
        python python-pip \
        portaudio \
        pulseaudio-utils \
        ffmpeg
else
    echo "请手动安装: python3, pip, portaudio, pulseaudio-utils, ffmpeg"
fi

# ---- 2. 创建 Python 虚拟环境 ----
echo ""
echo "[2/4] 创建 Python 虚拟环境..."

cd "$(dirname "$0")/.."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

# ---- 3. 配置虚拟音频设备 ----
echo ""
echo "[3/4] 配置 PulseAudio/PipeWire 虚拟音频设备..."

# 检测音频服务
if pactl info | grep -q "PipeWire"; then
    echo "检测到 PipeWire"
else
    echo "检测到 PulseAudio"
fi

# 检查是否已有虚拟设备
if pactl list short modules | grep -q "translator_virtual"; then
    echo "虚拟音频设备 'translator_virtual' 已存在，跳过创建。"
else
    # 创建 null sink（虚拟扬声器）
    pactl load-module module-null-sink \
        sink_name=translator_virtual_sink \
        sink_properties=device.description=Translator_Virtual_Speaker

    echo "✅ 虚拟扬声器已创建: translator_virtual_sink"
    echo "   - 用于: TTS合成语音输出 → 游戏麦克风输入"
    echo "   - Monitor源: translator_virtual_sink.monitor (捕获游戏声音)"
fi

# 列出音频设备
echo ""
echo "当前音频设备:"
python3 -c "
import sounddevice as sd
devices = sd.query_devices()
for i, d in enumerate(devices):
    in_ch = d['max_input_channels']
    out_ch = d['max_output_channels']
    print(f'  [{i}] {\"IN\" if in_ch>0 else \"  \"} {\"OUT\" if out_ch>0 else \"   \"} {d[\"name\"]}')
"

# ---- 4. 完成 ----
echo ""
echo "[4/4] 设置完成！"
echo ""
echo "============================================"
echo " 使用说明"
echo "============================================"
echo ""
echo "1. 激活虚拟环境:"
echo "   source venv/bin/activate"
echo ""
echo "2. 设置 API Key (选择一种方式):"
echo "   export OPENAI_API_KEY='sk-...'"
echo "   # 或在设置界面中输入"
echo ""
echo "3. 启动应用:"
echo "   python run.py"
echo ""
echo "4. 在"设置 -> 音频设备"中配置:"
echo "   - 麦克风: 你的物理麦克风"
echo "   - 游戏声音捕获: translator_virtual_sink.monitor"
echo "   - TTS输出设备: translator_virtual_sink"
echo ""
echo "5. 在游戏中设置麦克风输入为: translator_virtual_sink"
echo "============================================"
