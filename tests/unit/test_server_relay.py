"""Unit tests for PreviewRelay - real asyncio TCP sockets, no ffmpeg involved."""

from __future__ import annotations

import asyncio
import socket
import struct

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


async def test_peer_reset_does_not_raise_an_unhandled_task_exception() -> None:
    """A peer that resets (RST) rather than closing cleanly (FIN) surfaces as a
    ConnectionResetError from reader.read() inside _on_connect - what actually
    happens on Windows when ring.stop()'s taskkill /T kills ffmpeg mid-connection
    (the bug report this test guards against). _on_connect must catch it, not let
    it escape as an unhandled task exception."""
    relay = PreviewRelay()
    await relay.start(0)
    port = relay._server.sockets[0].getsockname()[1]

    loop = asyncio.get_running_loop()
    unhandled: list[object] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"data")
        await writer.drain()
        await asyncio.sleep(0.05)  # let _on_connect start its read

        sock = writer.get_extra_info("socket")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        writer.close()  # SO_LINGER(1, 0) turns this into a RST, not a FIN
        await asyncio.sleep(0.2)

        assert unhandled == []
    finally:
        loop.set_exception_handler(None)
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
