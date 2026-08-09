import subprocess
import sys
from pathlib import Path

import pytest

from loopwall.monitor.camera import build_input_args, detect_default_camera, list_camera_devices


def test_uvc_requires_device() -> None:
    with pytest.raises(ValueError, match="requires --device"):
        build_input_args("uvc", None, 1280, 720, 30)


def test_uvc_builds_v4l2_args() -> None:
    args = build_input_args("uvc", "/dev/video0", 1280, 720, 30)
    assert args == [
        "-f",
        "v4l2",
        "-video_size",
        "1280x720",
        "-framerate",
        "30",
        "-i",
        "/dev/video0",
    ]


def test_dshow_requires_device() -> None:
    with pytest.raises(ValueError, match="requires --device"):
        build_input_args("dshow", None, 1280, 720, 30)


def test_dshow_wraps_device_name() -> None:
    args = build_input_args("dshow", "My Camera", 640, 480, 15)
    assert args[-2:] == ["-i", "video=My Camera"]


def test_lavfi_default_source_matches_resolution_and_fps() -> None:
    args = build_input_args("lavfi", None, 640, 480, 24)
    assert args == ["-re", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=24"]


def test_lavfi_custom_source_overrides_default() -> None:
    args = build_input_args("lavfi", None, 640, 480, 24, lavfi_source="smptebars=size=320x240")
    assert args == ["-re", "-f", "lavfi", "-i", "smptebars=size=320x240"]


def test_unknown_camera_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown camera_type"):
        build_input_args("webcam-of-the-future", None, 1280, 720, 30)


_DSHOW_NEWER_FORMAT = (
    '[in#0 @ 0000] "Camera Sensor OV02C10" (video)\n'
    '[in#0 @ 0000]   Alternative name "@device_pnp_..."\n'
    '[in#0 @ 0000] "Microphone Array" (audio)\n'
)

_DSHOW_OLDER_FORMAT = (
    "[dshow @ 0000] DirectShow video devices (some may be both video and audio devices)\n"
    '[dshow @ 0000]  "Integrated Camera"\n'
    '[dshow @ 0000]    Alternative name "@device_pnp_..."\n'
    "[dshow @ 0000] DirectShow audio devices\n"
    '[dshow @ 0000]  "Microphone"\n'
)


def test_detect_default_camera_windows_newer_ffmpeg_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr=_DSHOW_NEWER_FORMAT),
    )
    assert detect_default_camera() == ("dshow", "Camera Sensor OV02C10")


def test_detect_default_camera_windows_older_ffmpeg_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr=_DSHOW_OLDER_FORMAT),
    )
    assert detect_default_camera() == ("dshow", "Integrated Camera")


def test_detect_default_camera_windows_no_devices_falls_back_to_lavfi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="nothing here"),
    )
    assert detect_default_camera() == ("lavfi", None)


def test_detect_default_camera_windows_ffmpeg_missing_falls_back_to_lavfi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    def _raise(*a, **k):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert detect_default_camera() == ("lavfi", None)


def test_detect_default_camera_linux_uses_dev_video0_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "exists", lambda self: True)
    assert detect_default_camera() == ("uvc", "/dev/video0")


def test_detect_default_camera_linux_falls_back_to_lavfi_without_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert detect_default_camera() == ("lavfi", None)


_DSHOW_MULTI_FORMAT = (
    '[in#0 @ 0000] "Camera Sensor OV02C10" (video)\n'
    '[in#0 @ 0000]   Alternative name "@device_pnp_..."\n'
    '[in#0 @ 0000] "USB2.0 HD UVC WebCam" (video)\n'
    '[in#0 @ 0000]   Alternative name "@device_pnp_..."\n'
    '[in#0 @ 0000] "Microphone Array" (audio)\n'
)


def test_list_camera_devices_windows_all_devices_not_just_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr=_DSHOW_MULTI_FORMAT),
    )
    assert list_camera_devices() == [
        ("dshow", "Camera Sensor OV02C10"),
        ("dshow", "USB2.0 HD UVC WebCam"),
    ]


def test_list_camera_devices_windows_older_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr=_DSHOW_OLDER_FORMAT),
    )
    assert list_camera_devices() == [("dshow", "Integrated Camera")]


def test_list_camera_devices_windows_none_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="nothing here"),
    )
    assert list_camera_devices() == []


def test_list_camera_devices_linux_globs_dev_video_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "glob", lambda self, pattern: iter([Path("/dev/video2"), Path("/dev/video0")]))
    assert list_camera_devices() == [("uvc", "/dev/video0"), ("uvc", "/dev/video2")]
