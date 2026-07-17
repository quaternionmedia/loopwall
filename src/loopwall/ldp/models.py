"""Pydantic LDP/1 event models (hello, transport, track, accent, capture_cue, clip_bind, scene, config_hint).

Built at 0.1.0 build order step 1 (ROADMAP.md): full catalog defined, only
hello/transport/track/capture_cue handled by core at that release
(accent/clip_bind/config_hint are logged and dropped; scene routes to
core/reconciler.py's registered stub, which raises).

One gap vs. PLAN.md §3.1's table, resolved rather than left implicit:
``transport`` carries no ``beats_per_bar`` there, but bar/beat math needs
one - added here, sourced from a driver's own time signature (the cli
driver reads it from the scenario's ``time_signature``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Capabilities(BaseModel):
    clock: Literal["external", "internal", "defer"]
    explicit_state: bool
    capture_cues: bool
    media_binding: bool
    style: Literal["event", "scene"]


class HelloEvent(BaseModel):
    t: Literal["hello"] = "hello"
    proto: Literal["ldp/1"] = "ldp/1"
    driver: str
    caps: Capabilities


class TransportEvent(BaseModel):
    t: Literal["transport"] = "transport"
    playing: bool
    bpm: float
    bar: int
    beat: float
    phase: float
    beats_per_bar: int = 4


class TrackEvent(BaseModel):
    t: Literal["track"] = "track"
    track: int
    state: Literal["empty", "recording", "playing", "silent"]
    pattern: int | None = None


class AccentEvent(BaseModel):
    t: Literal["accent"] = "accent"
    track: int
    velocity: int = Field(ge=0, le=127)


class CaptureCueEvent(BaseModel):
    t: Literal["capture_cue"] = "capture_cue"
    track: int
    pattern: int
    cycle_start_bar: int
    bars: int
    camera: str | None = None


class ClipBindEvent(BaseModel):
    t: Literal["clip_bind"] = "clip_bind"
    track: int
    pattern: int
    source: str  # file path, URL, or whep:// - PLAN.md §3.1


class Seat(BaseModel):
    track: int
    source: str
    label: str | None = None


class SceneEvent(BaseModel):
    t: Literal["scene"] = "scene"
    seats: list[Seat]
    quantize: str | None = None


class ConfigHintEvent(BaseModel):
    t: Literal["config_hint"] = "config_hint"
    names: dict[int, str] = Field(default_factory=dict)
    pattern_bars: dict[int, int] = Field(default_factory=dict)
    cameras: list[str] = Field(default_factory=list)


LdpEvent = Annotated[
    HelloEvent
    | TransportEvent
    | TrackEvent
    | AccentEvent
    | CaptureCueEvent
    | ClipBindEvent
    | SceneEvent
    | ConfigHintEvent,
    Field(discriminator="t"),
]
