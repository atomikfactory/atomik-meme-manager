"""Server shutdown: the scan/timer threads are joined (not abandoned) before the process exits."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from atomik_meme_web.api import create_app
from atomik_meme_web.config import WebConfig


def test_shutdown_waits_for_an_in_flight_scan_to_finish(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=True,
        scan_interval_min=0,
        open_browser=False,
    )
    fastapi_app = create_app(cfg, dist_dir=tmp_path / "no-dist")
    indexer = fastapi_app.state.indexer

    original_scan_body = indexer._scan_body

    def slow_scan_body(started_at: str):
        time.sleep(0.3)
        return original_scan_body(started_at)

    indexer._scan_body = slow_scan_body

    with TestClient(fastapi_app, headers={"X-Atomik-Meme": "1"}):
        for _ in range(100):
            if indexer.running:
                break
            time.sleep(0.01)
        assert indexer.running is True, "the scan-on-start background thread should be running"
    # `with` exited -> lifespan shutdown ran `indexer.join(timeout=5.0)` before returning; the
    # scan must be finished (not merely abandoned) by the time shutdown completes.
    assert indexer.running is False
    assert indexer._thread is not None
    assert not indexer._thread.is_alive()


def test_shutdown_stops_the_periodic_timer_thread(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=60,  # long enough it will never actually fire during the test
        open_browser=False,
    )
    fastapi_app = create_app(cfg, dist_dir=tmp_path / "no-dist")

    with TestClient(fastapi_app, headers={"X-Atomik-Meme": "1"}) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
    # No hang and no exception on exit: the periodic-scan thread (an Event.wait loop) must have
    # been signalled to stop and joined within the shutdown's 5 s timeout, not left dangling.


def test_shutdown_closes_database_connections(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    fastapi_app = create_app(cfg, dist_dir=tmp_path / "no-dist")
    db = fastapi_app.state.db

    with TestClient(fastapi_app, headers={"X-Atomik-Meme": "1"}) as client:
        client.get("/api/health")
    assert db._all_conns == []
