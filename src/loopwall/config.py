"""AppConfig: load config.example.yaml's shape, build a RingCapture from it.

Built at 0.1.0 build order step 5 (ROADMAP.md) - `loopwall play` needs a
config file describing camera/ring/session_root once, not a wall of CLI
flags repeating `monitor`/`demo`'s ad hoc options.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from loopwall.capture.ring import RingCapture
from loopwall.monitor.camera import build_input_args

# ADR-0003's ratified encoder name vs. ffmpeg's own -c:v value.
_ENCODER_MAP = {"x264": "libx264"}


class CliDriverConfig(BaseModel):
    bpm: float = 120.0


class DriverConfig(BaseModel):
    type: str = "cli"  # cli | hapax (1.0.0) | showrunner (1.1.0) - only "cli" exists at 0.1.0
    cli: CliDriverConfig = Field(default_factory=CliDriverConfig)


class CameraConfig(BaseModel):
    id: str
    type: str  # uvc | lavfi | dshow (monitor.camera.build_input_args' own vocabulary)
    device: str | None = None
    width: int = 1280
    height: int = 720
    fps: int = 30
    # Measured by `loopwall calibrate`, 0.2.0+ (config.example.yaml) - not
    # applied by anything at 0.1.0; modeled so it round-trips rather than
    # silently vanishing, same honesty STUBS.md asks of unhandled events.
    offset_ms: int = 0
    source: str | None = None  # lavfi source override

    @model_validator(mode="before")
    @classmethod
    def _split_res(cls, data: object) -> object:
        if isinstance(data, dict) and "res" in data:
            data = dict(data)
            width, height = data.pop("res").split("x")
            data.setdefault("width", int(width))
            data.setdefault("height", int(height))
        return data


class RingConfig(BaseModel):
    dir: Path
    seconds: float = 120.0


class CaptureConfig(BaseModel):
    cameras: list[CameraConfig]
    ring: RingConfig
    encoder: str = "x264"

    @property
    def default_camera(self) -> CameraConfig:
        # PLAN.md §4's ratified 0.1.0 default: single camera. The first
        # listed entry is it - nothing yet picks a camera by id.
        return self.cameras[0]


class ServerConfig(BaseModel):
    headless: bool = True


class AppConfig(BaseModel):
    driver: DriverConfig
    capture: CaptureConfig
    session_root: Path = Path("./sessions")
    server: ServerConfig = Field(default_factory=ServerConfig)


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    return AppConfig.model_validate(raw)


def build_ring_from_config(capture: CaptureConfig, segment_seconds: float = 2.0) -> RingCapture:
    """RingCapture for config's default camera - reuses monitor.camera's input-arg translation."""
    cam = capture.default_camera
    input_args = build_input_args(cam.type, cam.device, cam.width, cam.height, cam.fps, cam.source)
    return RingCapture(
        ring_dir=capture.ring.dir,
        input_args=input_args,
        segment_seconds=segment_seconds,
        ring_seconds=capture.ring.seconds,
        fps=cam.fps,
        encoder=_ENCODER_MAP.get(capture.encoder, capture.encoder),
    )
