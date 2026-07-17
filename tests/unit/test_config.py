from pathlib import Path

import yaml

from loopwall.config import AppConfig, build_ring_from_config, load_config

_MINIMAL = {
    "driver": {"type": "cli", "cli": {"bpm": 120}},
    "capture": {
        "cameras": [{"id": "cam0", "type": "uvc", "device": "/dev/video0", "res": "1280x720", "fps": 30}],
        "ring": {"dir": "/dev/shm/loopwall", "seconds": 120},
        "encoder": "x264",
    },
    "session_root": "./sessions",
    "server": {"headless": True},
}


def test_load_config_parses_the_repo_example() -> None:
    config = load_config(Path("config.example.yaml"))
    assert config.driver.type == "cli"
    assert config.capture.default_camera.id == "cam0"
    assert config.capture.default_camera.width == 1280
    assert config.capture.default_camera.height == 720
    assert config.capture.encoder == "x264"


def test_res_splits_into_width_and_height() -> None:
    config = AppConfig.model_validate(_MINIMAL)
    cam = config.capture.default_camera
    assert (cam.width, cam.height) == (1280, 720)


def test_explicit_width_height_beats_res_if_both_given() -> None:
    raw = {**_MINIMAL}
    raw["capture"] = {
        **raw["capture"],
        "cameras": [{"id": "cam0", "type": "uvc", "res": "1280x720", "width": 640, "height": 480}],
    }
    config = AppConfig.model_validate(raw)
    cam = config.capture.default_camera
    assert (cam.width, cam.height) == (640, 480)


def test_default_camera_is_first_in_list() -> None:
    raw = {**_MINIMAL}
    raw["capture"] = {
        **raw["capture"],
        "cameras": [
            {"id": "cam0", "type": "lavfi"},
            {"id": "cam1", "type": "lavfi"},
        ],
    }
    config = AppConfig.model_validate(raw)
    assert config.capture.default_camera.id == "cam0"


def test_build_ring_from_config_uses_default_camera(tmp_path: Path) -> None:
    raw = {
        **_MINIMAL,
        "capture": {
            "cameras": [{"id": "sim0", "type": "lavfi", "res": "640x480", "fps": 24}],
            "ring": {"dir": str(tmp_path / "ring"), "seconds": 60},
            "encoder": "x264",
        },
    }
    config = AppConfig.model_validate(raw)
    ring = build_ring_from_config(config.capture)
    assert ring.ring_dir == tmp_path / "ring"
    assert ring.ring_seconds == 60
    assert ring.encoder == "libx264"
    assert ring.input_args == ["-re", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=24"]


def test_load_config_roundtrips_a_written_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(_MINIMAL))
    config = load_config(path)
    assert config.session_root == Path("./sessions")
    assert config.server.headless is True
