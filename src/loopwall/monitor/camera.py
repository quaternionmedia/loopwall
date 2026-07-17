"""Translate a camera spec into ffmpeg input args for RingCapture.

`uvc` (V4L2) and `lavfi` match config.example.yaml's documented camera
types (production target: Linux). `dshow` is a Windows dev-machine
convenience, not a documented production type — see COOKBOOK.md.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def detect_default_camera() -> tuple[str, str | None]:
    """Best-effort real-camera default: ``(camera_type, device)`` for this platform.

    Onboarding (`loopwall play` with no scenario) should show a real
    camera without requiring a first-time user to go find its device name
    first — the opposite default from `monitor`/scripted `play`, which
    stay `lavfi` so those safe, read-only/scripted paths never touch
    hardware by accident. Returns ``("lavfi", None)`` if nothing could be
    found (no camera, ffmpeg missing, unsupported platform) — an honest
    fallback the caller is expected to surface, not a silent one.
    """
    if sys.platform == "win32":
        device = _first_dshow_video_device()
        return ("dshow", device) if device else ("lavfi", None)
    # Linux production target (config.example.yaml) - v4l2 devices are
    # conventionally /dev/video0 and up; check it actually exists rather
    # than just assuming.
    if Path("/dev/video0").exists():
        return ("uvc", "/dev/video0")
    return ("lavfi", None)


def _first_dshow_video_device(timeout: float = 5.0) -> str | None:
    """First video device name from ffmpeg's own dshow device listing, if any.

    ffmpeg's listing format has changed across versions: newer builds tag
    each device inline (``"name" (video)``); older ones group untagged
    names under a "DirectShow video devices" section header instead.
    Handles both rather than assuming one.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-f", "dshow", "-list_devices", "true", "-i", "dummy"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = result.stderr.splitlines()

    for line in lines:
        match = re.search(r'"([^"]+)"\s*\(video\)', line)
        if match:
            return match.group(1)

    in_video_section = False
    for line in lines:
        if "DirectShow video devices" in line:
            in_video_section = True
            continue
        if "DirectShow audio devices" in line:
            break
        if not in_video_section or "Alternative name" in line:
            continue
        match = re.search(r'"([^"]+)"', line)
        if match:
            return match.group(1)
    return None


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
