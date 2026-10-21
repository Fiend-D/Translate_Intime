"""A dropped Volcengine socket must reconnect instead of leaving a deaf client."""

from __future__ import annotations

import asyncio

from src.core.volc_engine import VolcASTClient


class _ClosedSocket:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _client(errors: list[str]) -> VolcASTClient:
    client = VolcASTClient(api_key="k", on_error=errors.append)
    client._running = True
    client._session_started.set()
    client._ws = _ClosedSocket()  # type: ignore[assignment]
    return client


async def test_lost_connection_triggers_reconnect(monkeypatch) -> None:
    errors: list[str] = []
    client = _client(errors)
    rotations: list[float] = []

    async def fake_rotate(*, reason: str = "", backoff_s: float = 0.0) -> bool:
        rotations.append(backoff_s)
        return True

    monkeypatch.setattr(client, "rotate_session", fake_rotate)

    await client._receive_loop()
    await asyncio.gather(*client._background)

    assert rotations == [1.5]
    assert client._reconnect_attempts == 0
    assert errors == []


async def test_reconnect_gives_up_with_error_after_backoff(monkeypatch) -> None:
    errors: list[str] = []
    client = _client(errors)
    client._max_reconnect = 3
    rotations: list[float] = []

    async def fake_rotate(*, reason: str = "", backoff_s: float = 0.0) -> bool:
        rotations.append(backoff_s)
        return False

    monkeypatch.setattr(client, "rotate_session", fake_rotate)

    await client._try_reconnect("connection-lost")

    assert rotations == [1.5, 3.0, 6.0]
    assert client.is_running is False
    assert errors and "连接中断" in errors[-1]


async def test_giving_up_reports_lost_channel_to_owner(monkeypatch) -> None:
    errors: list[str] = []
    lost: list[str] = []
    client = _client(errors)
    client.on_lost = lost.append
    client._max_reconnect = 1

    async def fake_rotate(*, reason: str = "", backoff_s: float = 0.0) -> bool:
        return False

    monkeypatch.setattr(client, "rotate_session", fake_rotate)

    await client._try_reconnect("connection-lost")

    assert lost and "重新开启通道" in lost[0]
    assert errors == []


async def test_close_cancels_pending_reconnect_and_stays_closed(monkeypatch) -> None:
    client = _client([])
    started = asyncio.Event()

    async def slow_rotate(*, reason: str = "", backoff_s: float = 0.0) -> bool:
        started.set()
        await asyncio.sleep(30)
        return True

    monkeypatch.setattr(client, "rotate_session", slow_rotate)
    client._schedule_reconnect("connection-lost")
    await started.wait()

    await client.close()

    assert not client._background
    assert client.is_running is False
    assert await client.connect() is False


async def test_failed_scheduled_rotation_falls_back_to_reconnect(monkeypatch) -> None:
    client = _client([])
    client.session_rotate_minutes = 1
    client._session_started_at = 1.0
    reasons: list[str] = []

    async def failing_rotate(*, reason: str = "", backoff_s: float = 0.0) -> bool:
        return False

    async def record_reconnect(reason: str = "") -> None:
        reasons.append(reason)
        client._running = False

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(client, "rotate_session", failing_rotate)
    monkeypatch.setattr(client, "_try_reconnect", record_reconnect)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    await client._rotate_loop()

    assert reasons == ["connection-lost"]
