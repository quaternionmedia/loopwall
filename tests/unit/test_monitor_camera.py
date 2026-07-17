import pytest

from loopwall.monitor.camera import build_input_args


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
