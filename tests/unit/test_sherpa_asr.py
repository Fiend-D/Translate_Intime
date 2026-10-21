"""Regression tests for local sherpa/SenseVoice recognition."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from src.engines.pipeline.sherpa_asr import SherpaOnnxAsr


class _OfflineStream:
    def __init__(self) -> None:
        self.result = SimpleNamespace(text="continuous speech")
        self.samples: np.ndarray | None = None

    def accept_waveform(self, sample_rate: int, samples: np.ndarray) -> None:
        assert sample_rate == 16000
        self.samples = samples


class _OfflineRecognizer:
    def __init__(self) -> None:
        self.decode_count = 0

    def create_stream(self) -> _OfflineStream:
        return _OfflineStream()

    def decode_stream(self, stream: _OfflineStream) -> None:
        assert stream.samples is not None
        self.decode_count += 1


def test_sensevoice_decodes_each_vad_gated_utterance_without_quiet_windows() -> None:
    asr = SherpaOnnxAsr.__new__(SherpaOnnxAsr)
    asr._model_id = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    recognizer = _OfflineRecognizer()
    # Upstream VAD commonly produces a steady voiced signal with no quiet gaps.
    pcm = np.full(16000 * 3, 4000, dtype=np.int16).tobytes()

    first = asr._recognize_with(recognizer, pcm, "en")
    second = asr._recognize_with(recognizer, pcm, "en")

    assert first == "continuous speech"
    assert second == "continuous speech"
    assert recognizer.decode_count == 2


class _OnlineStream:
    def __init__(self) -> None:
        self.chunks: list[np.ndarray] = []
        self.finished_after: int | None = None

    def accept_waveform(self, sample_rate: int, samples: np.ndarray) -> None:
        assert sample_rate == 16000
        assert self.finished_after is None
        self.chunks.append(samples)

    def input_finished(self) -> None:
        self.finished_after = len(self.chunks)


class _OnlineRecognizer:
    def __init__(self) -> None:
        self.stream = _OnlineStream()

    def create_stream(self) -> _OnlineStream:
        return self.stream

    def is_ready(self, _stream: _OnlineStream) -> bool:
        return False

    def get_result(self, _stream: _OnlineStream) -> str:
        return "THE MOST BEAUTIFUL"


def test_streaming_decode_pads_silence_before_finishing_input() -> None:
    asr = SherpaOnnxAsr.__new__(SherpaOnnxAsr)
    asr._model_id = "auto"
    recognizer = _OnlineRecognizer()
    pcm = np.full(16000, 4000, dtype=np.int16).tobytes()

    text = asr._recognize_with(recognizer, pcm, "en")

    stream = recognizer.stream
    assert text == "The most beautiful"
    assert stream.finished_after == 2
    tail = stream.chunks[-1]
    assert tail.size >= int(0.5 * 16000)
    assert not np.any(tail)
