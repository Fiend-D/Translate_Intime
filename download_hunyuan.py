#!/usr/bin/env python3
"""下载腾讯混元 HY-MT1.5-1.8B 模型到本地 models/ 目录"""
import os
from pathlib import Path
from huggingface_hub import snapshot_download

model_id = "tencent/HY-MT1.5-1.8B"
local_dir = Path(__file__).parent / "models" / "HY-MT1.5-1.8B"
local_dir.mkdir(parents=True, exist_ok=True)

print(f"开始下载 {model_id} 到 {local_dir} ...")
snapshot_download(
    repo_id=model_id,
    local_dir=str(local_dir),
    local_dir_use_symlinks=False,
    resume_download=True,
)
print(f"下载完成: {local_dir}")
