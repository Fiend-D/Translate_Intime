"""Downloaded model integrity helpers."""

from __future__ import annotations

import hashlib

from src.utils.file_integrity import matches_sha256, sha256_file


def test_sha256_file_matches_expected_digest(tmp_path) -> None:
    path = tmp_path / "model.bin"
    payload = b"trusted model bytes"
    path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()

    assert sha256_file(path) == expected
    assert matches_sha256(path, expected)
    assert not matches_sha256(path, "0" * 64)
    assert matches_sha256(path, ("0" * 64, expected))


def test_cached_digest_is_reused_until_file_changes(tmp_path, monkeypatch) -> None:
    from src.utils import file_integrity

    path = tmp_path / "model.bin"
    path.write_bytes(b"one")
    expected = hashlib.sha256(b"one").hexdigest()
    assert matches_sha256(path, expected)

    calls: list[object] = []
    real = file_integrity.sha256_file
    monkeypatch.setattr(file_integrity, "sha256_file", lambda p: calls.append(p) or real(p))
    assert matches_sha256(path, expected)
    assert calls == []

    path.write_bytes(b"two!")
    assert not matches_sha256(path, expected)
    assert calls == [path]
