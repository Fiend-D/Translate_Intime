"""Audio capture lifecycle tests without real hardware."""

from unittest.mock import MagicMock

from src.core.audio_capture import AudioCapture
from src.models.enums import Direction


def test_stream_read_failure_stops_capture_and_notifies() -> None:
    capture = AudioCapture(Direction.INBOUND, "fake-loopback")
    capture._audio_stream = MagicMock()
    capture._audio_stream.read_chunk.side_effect = RuntimeError("device removed")
    capture._running = True
    on_error = MagicMock()
    capture.on_error = on_error

    capture._poll_stream_loop()

    assert capture.is_running() is False
    on_error.assert_called_once()
    assert "device removed" in str(on_error.call_args.args[0])
