"""Unit tests for PreviewRelay - real asyncio TCP sockets, no ffmpeg involved."""

from __future__ import annotations

import asyncio

import pytest

from loopwall.server.relay import PreviewRelay

pytestmark = pytest.mark.anyio


async def _send(port: int, chunks: list[bytes]) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        for chunk in chunks:
            writer.write(chunk)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def test_subscriber_receives_bytes_sent_after_it_subscribes() -> None:
    relay = PreviewRelay()
    await relay.start(0)  # port 0 - let the OS pick a free one
    port = relay._server.sockets[0].getsockname()[1]

    try:
        sub = relay.subscribe()
        # Prime the async generator up to its first `await` so the queue exists
        # before anything is sent - otherwise the send could race the subscribe.
        first = asyncio.ensure_future(sub.__anext__())
        await asyncio.sleep(0.05)

        await _send(port, [b"hello", b"world"])

        # TCP is a byte stream, not message-framed - two writes on the
        # sender side can legitimately coalesce into one read here, so
        # only the concatenated total is guaranteed, not chunk boundaries.
        received = await first
        while received != b"helloworld":
            received += await sub.__anext__()
    finally:
        await relay.stop()


async def test_multiple_subscribers_each_get_every_chunk() -> None:
    relay = PreviewRelay()
    await relay.start(0)
    port = relay._server.sockets[0].getsockname()[1]

    try:
        sub_a = relay.subscribe()
        sub_b = relay.subscribe()
        first_a = asyncio.ensure_future(sub_a.__anext__())
        first_b = asyncio.ensure_future(sub_b.__anext__())
        await asyncio.sleep(0.05)

        await _send(port, [b"data"])

        assert await first_a == b"data"
        assert await first_b == b"data"
    finally:
        await relay.stop()


async def test_stop_ends_active_subscriptions() -> None:
    relay = PreviewRelay()
    await relay.start(0)

    sub = relay.subscribe()
    pending = asyncio.ensure_future(sub.__anext__())
    await asyncio.sleep(0.05)

    await relay.stop()

    with pytest.raises(StopAsyncIteration):
        await pending
