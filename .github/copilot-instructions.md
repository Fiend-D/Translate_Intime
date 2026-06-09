# Translator InTime - Copilot Instructions

Cross-platform real-time bidirectional game voice translation desktop application.

## Tech Stack
- Python 3.10+, PyQt6 GUI
- faster-whisper (ASR), edge-tts (TTS), multiple translation backends
- sounddevice + PyAudio for audio I/O
- Target: Windows 10/11, Ubuntu 22.04+

## Project Conventions
- Use type hints throughout
- Async patterns with asyncio for concurrent pipelines
- Configuration via YAML config files
- Modular architecture: src/core/, src/audio/, src/gui/, src/utils/
- Follow PEP 8 style
