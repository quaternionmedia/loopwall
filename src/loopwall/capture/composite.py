"""Basic grid composite render: stack the wall's currently-visible tiles into one video.

Same `cols = ceil(sqrt(N))` packed layout `wall/index.html`'s own JS uses (PLAN.md
§3.5) - not a preset, a live reflection of it, so a render always matches what was
actually on screen. A "basic" composite, not the full `packed`/`fixed16`/`brady9`
layout system with seat stickiness and reflow animation (STUBS.md, 0.2.0): every
clip loops to the *longest* clip's duration, scaled to one uniform cell size, no
cross-fade or seat assignment.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from loopwall.capture.extract import probe_duration


class CompositeError(Exception):
    pass


def composite_grid(
    clip_paths: list[Path],
    out_path: Path,
    cell_width: int = 480,
    cell_height: int = 270,
    encoder: str = "libx264",
    preset: str = "veryfast",
) -> Path:
    """Composite `clip_paths` into `out_path`, packed-grid layout, looped to the
    longest clip. Raises `CompositeError` with ffmpeg's own stderr on failure -
    same error-surfacing convention as `extract.extract_window`.

    Looping happens inside the filtergraph (the `loop` video filter, buffering
    already-*decoded* frames) rather than via `-stream_loop -1` on the input itself.
    Found the hard way, against a real imported clip (a 4K, VLC-muxed file with an
    edit list and an audio track - `WallDriver.import_clip`, not one of this
    project's own x264-veryfast-encoded captures): `-stream_loop` re-seeks the raw
    container back to the start on every loop, which failed outright on that file
    ("Missing key frame while searching for timestamp: 0", "Task finished with error
    code: -1") - a known fragility class for edit-list-having containers. The `loop`
    filter sidesteps container re-seeking entirely by looping frames already decoded
    once; putting it *after* `scale` in the chain means only the small, cell-sized
    frames are buffered, not the source's native resolution.
    """
    if not clip_paths:
        raise CompositeError("nothing to composite - no clips given")

    n = len(clip_paths)
    cols = math.ceil(math.sqrt(n))
    durations = [probe_duration(p) for p in clip_paths]
    max_duration = max(durations)
    # A generous frames-per-clip estimate for the loop filter's `size` (how many of
    # the input's own decoded frames to buffer and loop) - safe to overshoot a
    # clip's actual frame count (the filter just uses what exists), so a single
    # fixed high-end fps assumption covers everything from this project's own
    # 30fps captures to typical higher-fps imports without needing a per-clip
    # ffprobe just for frame rate.
    loop_sizes = [math.ceil(d * 60) for d in durations]

    cmd = ["ffmpeg", "-y"]
    for path in clip_paths:
        cmd += ["-i", str(path)]

    if n == 1:
        filter_complex = (
            f"[0:v]scale={cell_width}:{cell_height},setsar=1,loop=loop=-1:size={loop_sizes[0]}:start=0[out]"
        )
    else:
        scales = ";".join(
            f"[{i}:v]scale={cell_width}:{cell_height},setsar=1,loop=loop=-1:size={loop_sizes[i]}:start=0[v{i}]"
            for i in range(n)
        )
        layout = "|".join(f"{(i % cols) * cell_width}_{(i // cols) * cell_height}" for i in range(n))
        inputs = "".join(f"[v{i}]" for i in range(n))
        filter_complex = f"{scales};{inputs}xstack=inputs={n}:layout={layout}:fill=black[out]"

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-t",
        f"{max_duration:.3f}",
        "-c:v",
        encoder,
        "-preset",
        preset,
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise CompositeError(f"composite render failed: {result.stderr}")
    return out_path


def render_gif(src_path: Path, out_path: Path, fps: int = 12, width: int = 480) -> Path:
    """Converts an already-rendered video (typically `composite_grid`'s own mp4
    output, though any video works) into an animated GIF - the "git friendly" format
    a README splash demo actually needs: GitHub inlines a GIF directly from a
    relative repo path with no extra hosting and no player controls, unlike a
    committed `.mp4`, which doesn't reliably auto-embed that way.

    Standard palette approach, one ffmpeg invocation rather than two literal passes:
    `split` the scaled/resampled stream into two branches, `palettegen` on one to
    build a custom 256-color palette (`stats_mode=diff` weights it toward the pixels
    that actually change frame to frame, not a flat global histogram), `paletteuse`
    on the other against that palette - noticeably better color fidelity than
    ffmpeg's default fixed web-safe palette for a GIF this small.
    """
    filter_complex = (
        f"[0:v]scale={width}:-1:flags=lanczos,fps={fps},split[a][b];"
        f"[a]palettegen=stats_mode=diff[p];"
        f"[b][p]paletteuse=dither=bayer"
    )
    cmd = ["ffmpeg", "-y", "-i", str(src_path), "-filter_complex", filter_complex, str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise CompositeError(f"gif render failed: {result.stderr}")
    return out_path
