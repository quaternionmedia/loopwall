"""Console entry point. play/cli/replay land at 0.1.0 build order step 5 — see ROADMAP.md."""

import tempfile
import threading
import webbrowser
from pathlib import Path

import click
import uvicorn
import yaml
from rich.console import Console
from rich.live import Live

from loopwall import __version__
from loopwall.capture.ring import RingCapture
from loopwall.config import build_ring_from_config, load_config
from loopwall.core import Core
from loopwall.core.replay import replay_session
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.drivers.cli import CliDriver, Scenario
from loopwall.drivers.cli.wall import CameraSettings, WallDriver
from loopwall.midi import MidiInput
from loopwall.midi import is_available as midi_is_available
from loopwall.monitor import render as monitor_render
from loopwall.monitor.camera import build_input_args, detect_default_camera
from loopwall.monitor.state import monitor_state_from_core
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


@cli.command("midi-ports")
def midi_ports() -> None:
    """List MIDI input port names visible to mido - use one with `play --midi-port`."""
    if not midi_is_available():
        raise click.ClickException("mido isn't installed - run `uv sync --extra hapax`.")
    names = MidiInput.list_input_names()
    if not names:
        click.echo("No MIDI input ports found.")
        return
    for name in names:
        click.echo(name)


@cli.command()
@click.argument("session_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
def replay(session_dir: Path) -> None:
    """Replay SESSION_DIR's log.jsonl and verify it reproduces the same committed-op sequence.

    Reads only the driver's own `track`/`transport` events back through a
    fresh scheduler - no ring, no ffmpeg, no re-extraction - so this works
    even after the session's raw ring footage is long gone (PLAN.md §3.1's
    "every driver's LDP stream is ... replayable").
    """
    log_path = session_dir / "log.jsonl"
    if not log_path.exists():
        raise click.ClickException(f"no log.jsonl in {session_dir}")
    result = replay_session(log_path)
    if not result.matches:
        click.echo(
            f"MISMATCH: replay produced {len(result.replayed_ops)} committed ops, "
            f"log.jsonl recorded {len(result.logged_ops)}.",
            err=True,
        )
        raise click.ClickException("replay diverged from the original committed-op sequence")
    click.echo(f"OK: {len(result.replayed_ops)} committed ops replayed identically.")


def _run_scripted(scenario_path: Path, config_path: Path | None, monitor: bool = False) -> SessionArtifact:
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
        driver = CliDriver(core, scenario)
        if monitor:
            console = Console()
            with Live(
                monitor_render.render(monitor_state_from_core(ring, core.tracks, core.last_transport)),
                console=console,
                refresh_per_second=4,
            ) as live:
                driver.on_tick = lambda: live.update(
                    monitor_render.render(monitor_state_from_core(ring, core.tracks, core.last_transport))
                )
                driver.run()
        else:
            driver.run()
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
    midi: bool,
    midi_port: str | None,
    midi_beats_per_bar: int,
    midi_bindings: Path | None,
) -> SessionArtifact:
    if camera_type is None:
        camera_type, device = detect_default_camera()
        if camera_type == "lavfi":
            click.echo("No real camera found - falling back to a synthetic source.", err=True)

    # Resolved here, not left to build_ring()'s own default, so switch_camera() later
    # knows where the *original* ring lives and can put its own fresh directories
    # alongside it rather than guessing a possibly-different default.
    ring_dir = ring_dir or Path(tempfile.gettempdir()) / "loopwall" / "ring"
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
    # Stable across runs (unlike session_root's own children, one per run) - a
    # setup bound once, live, is there again next time without re-binding
    # anything (WallDriver.start()'s auto-restore).
    midi_bindings = midi_bindings or session_root.parent / "midi_bindings.json"
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

    midi_input: MidiInput | None = None
    if midi:
        if not midi_is_available():
            # --midi defaults to on (auto-attempt, like camera detection) so most runs
            # get here with nothing explicitly requested - only nag if the user actually
            # named a port, otherwise silently proceed without MIDI.
            if midi_port is not None:
                click.echo(
                    "MIDI requested but mido isn't installed - run `uv sync --extra hapax`. "
                    "Continuing without MIDI.",
                    err=True,
                )
        else:
            midi_input = MidiInput(beats_per_bar=midi_beats_per_bar, port_name=midi_port)

    camera = CameraSettings(
        camera_type=camera_type,
        device=device,
        width=width,
        height=height,
        fps=fps,
        ring_seconds=ring_seconds,
        segment_seconds=segment_seconds,
        ring_dir_base=ring_dir,
    )
    driver = WallDriver(
        core,
        ring,
        relay,
        session_root=session_root,
        camera=camera,
        midi=midi_input,
        midi_bindings_path=midi_bindings,
    )
    app = create_app(driver)

    url = f"http://127.0.0.1:{wall_port}/wall"
    click.echo(f"Wall running at {url} - Ctrl-C here to stop.")
    opener = threading.Timer(1.0, webbrowser.open, args=(url,))
    opener.daemon = True  # never itself block interpreter exit
    opener.start()
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=wall_port,
            log_level="warning",
            # /wall/preview.ts is a deliberately-infinite MPEG-TS stream (as
            # long as a browser tab has it open) - uvicorn's own graceful
            # shutdown otherwise waits forever (timeout_graceful_shutdown
            # defaults to None: `asyncio.wait_for(..., timeout=None)`,
            # confirmed by reading uvicorn/server.py directly) for exactly
            # that connection to finish on its own, which by design it
            # never will. This bounds that wait so Ctrl-C actually stops
            # the process instead of hanging until the browser tab closes.
            timeout_graceful_shutdown=3,
        )
    except KeyboardInterrupt:
        # Belt-and-suspenders: uvicorn installs its own SIGINT handler and
        # normally handles this itself, but if a KeyboardInterrupt reaches
        # here anyway, exit quietly rather than dumping a traceback.
        click.echo("\nStopped.")
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
@click.option(
    "--midi/--no-midi",
    default=True,
    show_default=True,
    help="Auto-attempt external MIDI clock/transport + an assignable start/stop trigger, "
    "same as camera auto-detect - connects to the first available port if one exists, "
    "otherwise the wall just runs without it (no SCENARIO only). Needs "
    "`uv sync --extra hapax` - see COOKBOOK.md. Use --no-midi to disable.",
)
@click.option(
    "--midi-port",
    default=None,
    help="MIDI input port name to use instead of auto-detecting the first one "
    "(run `loopwall midi-ports` to list what's available).",
)
@click.option(
    "--midi-beats-per-bar",
    default=4,
    show_default=True,
    help="Time signature numerator for external MIDI clock bar-boundary detection.",
)
@click.option(
    "--midi-bindings",
    "midi_bindings",
    type=click.Path(path_type=Path),
    default=None,
    help='Where the toggle trigger + camera-trigger bindings are saved to ("Save MIDI '
    'mappings" on the wall page) and auto-restored from on every future run - a '
    "controller bound once stays bound (no SCENARIO only). Defaults next to "
    "--session-root's own default, so it's stable across runs without needing to "
    "be named explicitly.",
)
@click.option(
    "--monitor",
    is_flag=True,
    default=False,
    help="Live Tier 0+1 terminal view (capture health, transport, per-track state - "
    "PLAN.md §3.6) while SCENARIO runs (SCENARIO only - the interactive wall's own "
    "status line covers this for the no-SCENARIO path; see COOKBOOK.md).",
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
    midi: bool,
    midi_port: str | None,
    midi_beats_per_bar: int,
    midi_bindings: Path | None,
    monitor: bool,
) -> None:
    """Run SCENARIO through the driver -> LDP -> core path, or start the live wall.

    With SCENARIO: replays a YAML script (`scenarios/smoke.yaml`) against
    `--config` - for testing/conformance, not a live take. See PLAN.md
    sections 3.2-3.3.

    With no SCENARIO: opens one browser window (the wall) showing a live
    camera preview tile; press space (or a learned MIDI trigger) to
    start/stop recording; each stop cuts a clip through the same real
    core path and adds it as a new, permanently looping tile - a growing
    grid built from your takes. Ctrl-C here to stop. See PLAN.md sections
    3.5, 3.7.

    Either way, a session artifact (log.jsonl + clips + index.json) is
    written and its directory printed on exit.
    """
    if not headless:
        raise click.UsageError("--no-headless needs the renderer, which lands at 0.2.0.")
    if monitor and scenario_path is None:
        raise click.UsageError(
            "--monitor needs SCENARIO - the wall's own status line covers no-SCENARIO runs."
        )

    if scenario_path is not None:
        session = _run_scripted(scenario_path, config_path, monitor)
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
            midi,
            midi_port,
            midi_beats_per_bar,
            midi_bindings,
        )

    session.finalize()
    click.echo(str(session.dir))


if __name__ == "__main__":
    cli()
