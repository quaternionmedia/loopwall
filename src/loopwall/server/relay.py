"""Raw MPEG-TS relay: forwards the ring's existing live-preview stream to the wall's browser tile as-is.

`RingCapture` (see its own docstring) already tees an h264/MPEG-TS stream
to a local TCP port - a viewer listens, the ring's ffmpeg connects out as
a client. An earlier version of this module spawned a second ffmpeg
process to *decode* that stream and *re-encode* it as MJPEG for the
browser; that round trip (on top of the ring's own encoder buffering,
capture/ring.py's `-tune zerolatency` comment) was the largest source of
the wall's live-preview lag. This version does no transcoding at all -
plain asyncio TCP server in, raw bytes out to any subscriber - and the
browser decodes the h264 itself via `mpegts.js` (`wall/mpegts.js`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class PreviewRelay:
    """Accepts RingCapture's outbound TCP connection and fans its raw bytes out to subscribers.

    Being our own asyncio server rather than an external ffmpeg process
    listening on the port means readiness is just "the `start()` await
    returned" - no netstat/ss polling loop needed the way waiting on an
    external process's bind required (see git history for that version).
    """

    def __init__(self) -> None:
        self._server: asyncio.base_events.Server | None = None
        self._queues: list[asyncio.Queue[bytes | None]] = []

    async def start(self, port: int) -> None:
        self._server = await asyncio.start_server(self._on_connect, "127.0.0.1", port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for queue in self._queues:
            queue.put_nowait(None)

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    chunk = await reader.read(65536)
                except (ConnectionResetError, ConnectionAbortedError, OSError):
                    # ffmpeg (the ring's outbound peer here) getting killed rather than
                    # exiting cleanly - e.g. ring.stop()'s taskkill /T on Windows - sends
                    # a TCP RST instead of a FIN. Treat it the same as a clean EOF.
                    return
                if not chunk:
                    return
                for queue in list(self._queues):
                    queue.put_nowait(chunk)
        finally:
            writer.close()

    async def subscribe(self) -> AsyncIterator[bytes]:
        """Yield each byte chunk as it arrives. A new subscriber starts from "now" - live, not seekable."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._queues.append(queue)
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    return
                yield chunk
        finally:
            self._queues.remove(queue)
