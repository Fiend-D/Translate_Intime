# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['D:\\WorkSpace\\personal_tool\\translator_intime\\run.py'],
    pathex=[],
    binaries=[],
    datas=[('python_protogen', 'python_protogen'), ('config', 'config'), ('assets', 'assets'), ('hotwords', 'hotwords'), ('specs', 'specs')],
    hiddenimports=['PyQt6', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets', 'pyaudio', 'sounddevice', 'soundfile', 'numpy', 'scipy', 'scipy.signal', 'pydantic', 'loguru', 'httpx', 'aiohttp', 'protobuf', 'keyring', 'pynput', 'pynput.keyboard', 'pynput.keyboard._win32', 'pynput.mouse', 'pynput.mouse._win32', 'yaml', 'soundcard', 'dotenv', 'websockets', 'edge_tts', 'dashscope', 'dashscope.audio.asr', 'comtypes', 'pycaw', 'pycaw.pycaw', 'audioop', 'src.core.speech_gate', 'src.core.usage_tracker', 'src.core.audio_capture', 'src.core.audio_player', 'src.core.exceptions', 'src.core.pipeline', 'src.core.volc_engine', 'src.core.dota_coach', 'src.core.music_share', 'src.core.typed_translate', 'src.engines', 'src.engines.base', 'src.engines.factory', 'src.engines.volc', 'src.engines.volc.engine', 'src.engines.pipeline', 'src.engines.pipeline.engine', 'src.engines.pipeline.asr', 'src.engines.pipeline.mt', 'src.engines.pipeline.tts', 'src.engines.pipeline.nllb_mt', 'src.engines.pipeline.kokoro_tts', 'src.engines.pipeline.model_catalog', 'src.engines.pipeline.utterance', 'src.core.silero_vad', 'src.core.quality_presets', 'src.core.audio_pre_roll', 'src.audio.stream', 'src.audio.device_guard', 'src.audio.session_ducker', 'src.audio.wasapi_process_loopback', 'src.audio.virtual_device', 'src.gui.main_window', 'src.gui.subtitle_overlay', 'src.gui.subtitle_buffer', 'src.gui.styles', 'src.gui.hotkey_dialog', 'src.gui.hotkey_edit', 'src.gui.device_labels', 'src.gui.corpus_dialog', 'src.gui.music_sidebar', 'src.gui.toast', 'src.gui.typed_dialog', 'src.gui.offline_model_dialog', 'src.models', 'src.models.config', 'src.models.enums', 'src.models.internal', 'src.models.session', 'src.models.subtitle', 'src.utils.logger', 'src.utils.config_manager', 'src.utils.hotkeys', 'src.utils.proxy_env', 'src.utils.audio_utils', 'src.utils.hotword_files', 'python_protogen', 'python_protogen.common.events_pb2', 'python_protogen.common.events_pb2_grpc', 'python_protogen.common.rpcmeta_pb2', 'python_protogen.common.rpcmeta_pb2_grpc', 'python_protogen.products.understanding.ast.ast_service_pb2', 'python_protogen.products.understanding.ast.ast_service_pb2_grpc', 'python_protogen.products.understanding.base.au_base_pb2', 'python_protogen.products.understanding.base.au_base_pb2_grpc'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SoundFerry',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:\\WorkSpace\\personal_tool\\translator_intime\\assets\\app_icon.ico'],
)
