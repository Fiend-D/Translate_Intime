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
