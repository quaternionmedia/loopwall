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
