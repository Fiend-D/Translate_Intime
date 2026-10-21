def test_dedupe_memory_forgets_oldest_lines_only() -> None:
    from src.engines.pipeline import live_captions_asr as lc

    asr = lc.LiveCaptionsAsr()
    for i in range(lc._EMITTED_MEMORY + 20):
        asr._remember_emitted(f"line {i}")

    assert "line 0" not in asr._emitted_lines
    assert f"line {lc._EMITTED_MEMORY + 19}" in asr._emitted_lines
    assert len(asr._emitted_lines) == lc._EMITTED_MEMORY


def test_stop_allows_a_failed_launch_to_retry() -> None:
    from src.engines.pipeline import live_captions_asr as lc

    asr = lc.LiveCaptionsAsr()
    asr._failed = True
    asr.stop()

    assert asr._failed is False
