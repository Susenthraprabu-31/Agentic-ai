"""Local job runner notification and in-process IPC tests."""

import pytest

from app.workers.inprocess_ipc import (
    enqueue_live_input,
    is_cancel_flag_set,
    publish_live_frame,
    set_cancel_flag,
    use_inprocess_ipc,
)
from app.workers.job_runner import notify_job_runner


@pytest.mark.asyncio
async def test_inprocess_cancel_and_frames():
    from app.core.config import settings

    if not settings.USE_REDIS:
        assert use_inprocess_ipc() or settings.DESKTOP_MODE
    await set_cancel_flag("test-run")
    assert await is_cancel_flag_set("test-run")
    await publish_live_frame("test-run", b"jpeg", {"url": "http://example.com"}, 60)
    ok = await enqueue_live_input("test-run", '{"action":"click","x":1,"y":1,"client_width":100,"client_height":100}')
    assert ok


def test_notify_job_runner_does_not_raise():
    notify_job_runner()