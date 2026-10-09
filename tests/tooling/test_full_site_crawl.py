import asyncio
import time
from threading import Event
from types import SimpleNamespace

import pytest
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_frontier import CrawlRunLimits
from signal_core.crawl_workflow_activities import CrawlExecutionRetryable
from signal_core.full_site_crawl import FullSiteCrawlExecutor


@pytest.mark.anyio
async def test_cancellation_waits_for_crawl_thread_to_stop_before_returning(monkeypatch):
    started = Event()
    stopped = Event()
    executor = object.__new__(FullSiteCrawlExecutor)

    def run(command, *, first_run_id, progress, cancelled):
        assert command == "synthetic-command"
        assert first_run_id == "synthetic-run"
        started.set()
        while not cancelled.is_set():
            time.sleep(0.01)
        stopped.set()
        raise CrawlExecutionRetryable()

    monkeypatch.setattr(executor, "run", run)
    monkeypatch.setattr(
        "signal_core.full_site_crawl.activity.info",
        lambda: SimpleNamespace(workflow_run_id="synthetic-run"),
    )
    task = asyncio.create_task(executor.execute("synthetic-command", heartbeat=lambda _: None))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert stopped.is_set()


def test_executor_rejects_a_duration_beyond_the_temporal_activity_budget(tmp_path):
    with pytest.raises(ValueError, match="Validated full-site crawler"):
        FullSiteCrawlExecutor(
            admission_connection_factory=lambda: None,
            ingest_connection_factory=lambda: None,
            store=EncryptedLocalArtifactStore(tmp_path / "objects"),
            artifact_key=ArtifactEncryptionKey("local-test-key", b"k" * 32),
            fetcher=SimpleNamespace(request=lambda *_args, **_kwargs: None),
            network_profile_sha256="ab" * 32,
            worker_key="worker.test",
            limits=CrawlRunLimits(max_duration_seconds=3601),
        )
