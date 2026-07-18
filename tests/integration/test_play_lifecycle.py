"""End-to-end `loopwall play` lifecycle via Click's CliRunner against a real
`lavfi` ring and a small, fast fixture scenario - not smoke.yaml's real-time
32s run, which stays reserved for the eventual hardware gate
(tests/integration/test_smoke.py).

Not `hardware`-marked - lavfi never touches real capture hardware, so this
is safe to run anywhere ffmpeg is installed; skipped, not failed, when it
isn't (matching test_monitor_lifecycle.py's convention).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from loopwall.__main__ import cli

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _write_config(path: Path, ring_dir: Path, session_root: Path, driver_type: str = "cli") -> None:
    config = {
        "driver": {"type": driver_type, "cli": {"bpm": 600}},
        "capture": {
            "cameras": [{"id": "sim0", "type": "lavfi", "res": "320x240", "fps": 15}],
            "ring": {"dir": str(ring_dir), "seconds": 30},
            "encoder": "x264",
        },
        "session_root": str(session_root),
        "server": {"headless": True},
    }
    path.write_text(yaml.safe_dump(config))


def _write_scenario(path: Path) -> None:
    scenario = {
        "name": "fixture",
        "bpm": 600,  # compressed so the whole run finishes in ~1s
        "time_signature": "4/4",
        "script": [
            {"at_bar": 0, "do": "play"},
            {"at_bar": 1, "do": {"track": 1, "record": {"bars": 2}}},
            {"at_bar": 3, "do": "stop"},
        ],
    }
    path.write_text(yaml.safe_dump(scenario))


def test_play_produces_a_session_with_one_clip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    scenario_path = tmp_path / "fixture.yaml"
    _write_config(config_path, tmp_path / "ring", tmp_path / "sessions")
    _write_scenario(scenario_path)

    result = CliRunner().invoke(cli, ["play", str(scenario_path), "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    session_dir = Path(result.output.strip())
    assert session_dir.is_dir()
    index = json.loads((session_dir / "index.json").read_text())
    assert len(index["clips"]) == 1
    assert index["clips"][0]["track"] == 1
    assert (session_dir / "log.jsonl").exists()


def test_play_monitor_flag_shows_live_tier1_state(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    scenario_path = tmp_path / "fixture.yaml"
    _write_config(config_path, tmp_path / "ring", tmp_path / "sessions")
    _write_scenario(scenario_path)

    result = CliRunner().invoke(cli, ["play", str(scenario_path), "--config", str(config_path), "--monitor"])

    assert result.exit_code == 0, result.output
    assert "capture (Tier 0)" in result.output
    assert "arrangement (Tier 1)" in result.output
    assert "track 1" in result.output
    assert "recording" in result.output or "playing" in result.output


def test_play_monitor_without_scenario_raises_usage_error() -> None:
    result = CliRunner().invoke(cli, ["play", "--monitor"])
    assert result.exit_code != 0
    assert "--monitor needs SCENARIO" in result.output


def test_play_no_headless_raises_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    scenario_path = tmp_path / "fixture.yaml"
    _write_config(config_path, tmp_path / "ring", tmp_path / "sessions")
    _write_scenario(scenario_path)

    result = CliRunner().invoke(
        cli, ["play", str(scenario_path), "--config", str(config_path), "--no-headless"]
    )
    assert result.exit_code != 0
    assert "0.2.0" in result.output


def test_play_unsupported_driver_type_raises_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    scenario_path = tmp_path / "fixture.yaml"
    _write_config(config_path, tmp_path / "ring", tmp_path / "sessions", driver_type="hapax")
    _write_scenario(scenario_path)

    result = CliRunner().invoke(cli, ["play", str(scenario_path), "--config", str(config_path)])
    assert result.exit_code != 0
    assert "hapax" in result.output


def test_play_scenario_without_config_raises_usage_error(tmp_path: Path) -> None:
    scenario_path = tmp_path / "fixture.yaml"
    _write_scenario(scenario_path)

    result = CliRunner().invoke(cli, ["play", str(scenario_path)])
    assert result.exit_code != 0
    assert "--config" in result.output


# The no-SCENARIO path now hands off to `uvicorn.run(...)`, which blocks
# until Ctrl-C/shutdown - not something a quick CliRunner invocation can
# exercise. `_run_interactive`'s own pieces (WallDriver, the FastAPI app)
# are covered directly instead: tests/integration/test_wall_app.py.


def test_midi_ports_without_mido_installed_raises_a_clear_error() -> None:
    """This dev/CI machine doesn't have mido installed (see midi.py's own docstring on
    why it's a soft dependency) - that's exactly the path this asserts, since it's the
    only one exercisable without real hardware."""
    import loopwall.midi as midi_module

    if midi_module.is_available():
        pytest.skip("mido is installed in this environment - the not-installed path doesn't apply")

    result = CliRunner().invoke(cli, ["midi-ports"])
    assert result.exit_code != 0
    assert "hapax" in result.output
