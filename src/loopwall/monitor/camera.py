"""Translate a camera spec into ffmpeg input args for RingCapture.

`uvc` (V4L2) and `lavfi` match config.example.yaml's documented camera
types (production target: Linux). `dshow` is a Windows dev-machine
convenience, not a documented production type — see COOKBOOK.md.
"""

from __future__ import annotations


def build_input_args(
    camera_type: str,
    device: str | None,
    width: int,
    height: int,
    fps: int,
    lavfi_source: str | None = None,
) -> list[str]:
    if camera_type == "uvc":
        if not device:
            raise ValueError("uvc camera type requires --device (e.g. /dev/video0)")
        return [
            "-f",
            "v4l2",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(fps),
            "-i",
            device,
        ]
    if camera_type == "dshow":
        if not device:
            raise ValueError(
                "dshow camera type requires --device (see `ffmpeg -f dshow -list_devices true -i dummy`)"
            )
        return [
            "-f",
            "dshow",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(fps),
            "-i",
            f"video={device}",
        ]
    if camera_type == "lavfi":
        source = lavfi_source or f"testsrc2=size={width}x{height}:rate={fps}"
        # -re paces the input to real-time; without it, ffmpeg processes a
        # synthetic source as fast as the CPU allows, racing through
        # max_runtime_seconds in a fraction of a real second (confirmed the
        # hard way - a ring reading "15s" of content after a 4s sleep).
        return ["-re", "-f", "lavfi", "-i", source]
    raise ValueError(f"unknown camera_type: {camera_type!r} (expected uvc, dshow, or lavfi)")
