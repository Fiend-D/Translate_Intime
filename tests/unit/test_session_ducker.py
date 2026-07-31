"""Windows audio-session ducking behavior without touching real sessions."""

import os

from src.audio import session_ducker
from src.audio.session_ducker import SessionDucker


class _Volume:
    def __init__(self, gain: float, muted: bool = False) -> None:
        self.gain = gain
        self.muted = muted

    def GetMasterVolume(self) -> float:  # noqa: N802
        return self.gain

    def SetMasterVolume(self, gain: float, _context) -> None:  # noqa: ANN001, N802
        self.gain = gain

    def GetMute(self) -> bool:  # noqa: N802
        return self.muted

    def SetMute(self, muted: int, _context) -> None:  # noqa: ANN001, N802
        self.muted = bool(muted)


class _Session:
    def __init__(self, key: str, pid: int, gain: float, muted: bool = False) -> None:
        self.InstanceIdentifier = key
        self.ProcessId = pid
        self.SimpleAudioVolume = _Volume(gain, muted)


def test_duck_reduces_other_sessions_and_restores_them(monkeypatch) -> None:  # noqa: ANN001
    own = _Session("own", os.getpid(), 0.9)
    game = _Session("game", 1234, 0.8)
    quiet = _Session("quiet", 2345, 0.1)
    sessions = [own, game, quiet]
    monkeypatch.setattr(session_ducker, "_get_audio_sessions", lambda: sessions)
    ducker = SessionDucker(mode="duck", duck_gain=0.2)

    ducker.pulse()

    assert ducker.is_active is True
    assert own.SimpleAudioVolume.gain == 0.9
    assert game.SimpleAudioVolume.gain == 0.2
    assert quiet.SimpleAudioVolume.gain == 0.1

    ducker.release()

    assert ducker.is_active is False
    assert game.SimpleAudioVolume.gain == 0.8
    assert quiet.SimpleAudioVolume.gain == 0.1


def test_mute_restores_previous_volume_and_mute_state(monkeypatch) -> None:  # noqa: ANN001
    game = _Session("game", 1234, 0.65, muted=False)
    monkeypatch.setattr(session_ducker, "_get_audio_sessions", lambda: [game])
    ducker = SessionDucker(mode="mute")

    ducker.pulse()
    assert game.SimpleAudioVolume.muted is True

    ducker.close()
    assert game.SimpleAudioVolume.gain == 0.65
    assert game.SimpleAudioVolume.muted is False
