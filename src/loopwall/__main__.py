"""Console entry point. play/cli/replay land at 0.1.0 build order step 5 — see ROADMAP.md."""

import tempfile
from pathlib import Path

import click

from loopwall import __version__
from loopwall.capture.ring import RingCapture
from loopwall.monitor import render as monitor_render
from loopwall.monitor.camera import build_input_args


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="loopwall")
@click.pass_context
def cli(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.option(
    "--camera-type",
    type=click.Choice(["uvc", "dshow", "lavfi"]),
    default="lavfi",
    show_default=True,
    help="uvc=V4L2 (Linux production target), dshow=Windows dev convenience, "
    "lavfi=synthetic (no hardware touched).",
)
@click.option(
    "--device",
    default=None,
    help="Device path (uvc, e.g. /dev/video0) or name (dshow) - see COOKBOOK.md recipe 3.",
)
@click.option("--lavfi-source", default=None, help="Only for --camera-type lavfi.")
@click.option("--width", default=1280, show_default=True)
@click.option("--height", default=720, show_default=True)
@click.option("--fps", default=30, show_default=True)
@click.option(
    "--ring-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Defaults to a temp dir. Production (Linux) should point this at tmpfs - see config.example.yaml.",
)
@click.option("--ring-seconds", default=120.0, show_default=True)
@click.option("--segment-seconds", default=2.0, show_default=True)
@click.option("--refresh-hz", default=2.0, show_default=True)
def monitor(
    camera_type: str,
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
    ring_dir = ring_dir or Path(tempfile.gettempdir()) / "loopwall" / "ring"
    input_args = build_input_args(camera_type, device, width, height, fps, lavfi_source)
    ring = RingCapture(
        ring_dir=ring_dir,
        input_args=input_args,
        segment_seconds=segment_seconds,
        ring_seconds=ring_seconds,
        fps=fps,
    )
    with ring:
        try:
            monitor_render.run(ring, refresh_hz=refresh_hz)
        except KeyboardInterrupt:
            click.echo("\nStopped.")


if __name__ == "__main__":
    cli()
