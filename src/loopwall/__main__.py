"""Console entry point. play/cli/replay land at 0.1.0 build order step 5 — see ROADMAP.md."""

import tempfile
import threading
import webbrowser
from pathlib import Path

import click
import uvicorn
import yaml

from loopwall import __version__
from loopwall.capture.ring import RingCapture
from loopwall.config import build_ring_from_config, load_config
from loopwall.core import Core
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.drivers.cli import CliDriver, Scenario
from loopwall.drivers.cli.wall import WallDriver
from loopwall.monitor import render as monitor_render
from loopwall.monitor.camera import build_input_args, detect_default_camera
from loopwall.server.app import create_app
from loopwall.server.relay import PreviewRelay


def camera_options(f):
    """Shared camera/ring options for any command that attaches a RingCapture.

    ``--camera-type`` defaults to unset - callers resolve their own
    fallback (``monitor`` stays safely synthetic; ``play``'s interactive
    mode prefers a real, auto-detected camera, since onboarding with a
    fake video defeats the point).
    """
    options = [
        click.option(
            "--camera-type",
            type=click.Choice(["uvc", "dshow", "lavfi"]),
            default=None,
            help="uvc=V4L2 (Linux production target), dshow=Windows, lavfi=synthetic. "
            "Defaults to whatever the command itself falls back to when unset.",
        ),
        click.option(
            "--device",
            default=None,
            help="Device path (uvc, e.g. /dev/video0) or name (dshow) - see COOKBOOK.md.",
        ),
        click.option("--lavfi-source", default=None, help="Only for --camera-type lavfi."),
        click.option("--width", default=1280, show_default=True),
        click.option("--height", default=720, show_default=True),
        click.option("--fps", default=30, show_default=True),
        click.option(
            "--ring-dir",
            type=click.Path(path_type=Path),
            default=None,
            help="Defaults to a temp dir. Production (Linux) should point this at tmpfs - "
            "see config.example.yaml.",
        ),
        click.option("--ring-seconds", default=120.0, show_default=True),
        click.option("--segment-seconds", default=2.0, show_default=True),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def build_ring(
    camera_type: str,
    device: str | None,
    lavfi_source: str | None,
    width: int,
    height: int,
    fps: int,
    ring_dir: Path | None,
    ring_seconds: float,
    segment_seconds: float,
    preview_tcp_port: int | None = None,
) -> RingCapture:
    ring_dir = ring_dir or Path(tempfile.gettempdir()) / "loopwall" / "ring"
    input_args = build_input_args(camera_type, device, width, height, fps, lavfi_source)
    return RingCapture(
        ring_dir=ring_dir,
        input_args=input_args,
        segment_seconds=segment_seconds,
        ring_seconds=ring_seconds,
        fps=fps,
        preview_tcp_port=preview_tcp_port,
    )


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="loopwall")
@click.pass_context
def cli(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@camera_options
@click.option("--refresh-hz", default=2.0, show_default=True)
def monitor(
    camera_type: str | None,
    device: str | None,
    lavfi_source: str | None,
    width: int,
    height: int,
    fps: int,
    ring_dir: Path | None,
    ring_seconds: float,
    segment_seconds: float,
    refresh_hz: float,
) -> None:
    """Tier 0 live capture-ring health monitor, read-only. See PLAN.md section 3.6.

    Defaults to a synthetic `lavfi` source so running this command never
    touches a real camera by accident - pass --camera-type uvc/dshow with
    --device to point it at real hardware.
    """
    ring = build_ring(
        camera_type or "lavfi",
        device,
        lavfi_source,
        width,
        height,
        fps,
        ring_dir,
        ring_seconds,
        segment_seconds,
    )
    with ring:
        try:
            monitor_render.run(ring, refresh_hz=refresh_hz)
        except KeyboardInterrupt:
            click.echo("\nStopped.")


def _run_scripted(scenario_path: Path, config_path: Path | None) -> SessionArtifact:
    if config_path is None:
        raise click.UsageError("SCENARIO requires --config (see config.example.yaml).")
    app_config = load_config(config_path)
    if app_config.driver.type != "cli":
        raise click.UsageError(
            f"driver.type={app_config.driver.type!r} isn't supported yet (only 'cli' at 0.1.0)."
        )

    scenario = Scenario.model_validate(yaml.safe_load(scenario_path.read_text()))
    ring = build_ring_from_config(app_config.capture)
    session = SessionArtifact(
        root=app_config.session_root,
        bpm=scenario.bpm,
        beats_per_bar=scenario.beats_per_bar,
        scenario=scenario.name,
    )
    core = Core(
        timeline=Timeline(),
        tracks=TrackRegistry(),
        scheduler=Scheduler(),
        session=session,
        ring=ring,
        default_camera_id=app_config.capture.default_camera.id,
    )
    with ring:
        CliDriver(core, scenario).run()
    return session


def _run_interactive(
    camera_type: str | None,
    device: str | None,
    lavfi_source: str | None,
    width: int,
    height: int,
    fps: int,
    ring_dir: Path | None,
    ring_seconds: float,
    segment_seconds: float,
    session_root: Path | None,
    preview_port: int,
    wall_port: int,
) -> SessionArtifact:
    if camera_type is None:
        camera_type, device = detect_default_camera()
        if camera_type == "lavfi":
            click.echo("No real camera found - falling back to a synthetic source.", err=True)

    ring = build_ring(
        camera_type,
        device,
        lavfi_source,
        width,
        height,
        fps,
        ring_dir,
        ring_seconds,
        segment_seconds,
        preview_tcp_port=preview_port,
    )
    session_root = session_root or Path(tempfile.gettempdir()) / "loopwall" / "sessions"
    # bpm/beats_per_bar aren't musically meaningful here - WallDriver ticks
    # its own internal quantization clock (see its own docstring), not a
    # real tempo; these are just descriptive SessionIndex metadata.
    session = SessionArtifact(root=session_root, bpm=0.0, beats_per_bar=1)
    core = Core(
        timeline=Timeline(),
        tracks=TrackRegistry(),
        scheduler=Scheduler(),
        session=session,
        ring=ring,
        default_camera_id="cam0",
    )
    relay = PreviewRelay()
    driver = WallDriver(core, ring, relay)
    app = create_app(driver, clips_dir=session.dir)

    url = f"http://127.0.0.1:{wall_port}/wall"
    click.echo(f"Wall running at {url} - Ctrl-C here to stop.")
    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=wall_port, log_level="warning")
    return session


@cli.command()
@click.argument(
    "scenario_path", metavar="[SCENARIO]", required=False, type=click.Path(path_type=Path, exists=True)
)
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="See config.example.yaml. Required with SCENARIO; ignored otherwise.",
)
@camera_options
@click.option(
    "--session-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Where session artifacts land (no SCENARIO only). Defaults to a temp dir.",
)
@click.option(
    "--preview-port",
    default=17123,
    show_default=True,
    help="Local TCP port between the ring and the preview relay (no SCENARIO only).",
)
@click.option(
    "--wall-port",
    default=8000,
    show_default=True,
    help="Local HTTP port for the wall page (no SCENARIO only).",
)
def play(
    scenario_path: Path | None,
    headless: bool,
    config_path: Path | None,
    camera_type: str | None,
    device: str | None,
    lavfi_source: str | None,
    width: int,
    height: int,
    fps: int,
    ring_dir: Path | None,
    ring_seconds: float,
    segment_seconds: float,
    session_root: Path | None,
    preview_port: int,
    wall_port: int,
) -> None:
    """Run SCENARIO through the driver -> LDP -> core path, or start the live wall.

    With SCENARIO: replays a YAML script (`scenarios/smoke.yaml`) against
    `--config` - for testing/conformance, not a live take. See PLAN.md
    sections 3.2-3.3.

    With no SCENARIO: opens one browser window (the wall) showing a live
    camera preview tile; press space to start/stop recording; each stop
    cuts a clip through the same real core path and adds it as a new,
    permanently looping tile - a growing grid built from your takes.
    Ctrl-C here to stop. See PLAN.md sections 3.5, 3.7.

    Either way, a session artifact (log.jsonl + clips + index.json) is
    written and its directory printed on exit.
    """
    if not headless:
        raise click.UsageError("--no-headless needs the renderer, which lands at 0.2.0.")

    if scenario_path is not None:
        session = _run_scripted(scenario_path, config_path)
    else:
        session = _run_interactive(
            camera_type,
            device,
            lavfi_source,
            width,
            height,
            fps,
            ring_dir,
            ring_seconds,
            segment_seconds,
            session_root,
            preview_port,
            wall_port,
        )

    session.finalize()
    click.echo(str(session.dir))


if __name__ == "__main__":
    cli()
