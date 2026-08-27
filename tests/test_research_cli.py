from __future__ import annotations

from research.cli import main


def test_cli_status_runs_with_temp_paths(tmp_path, capsys) -> None:
    code = main([
        "--artifact-dir",
        str(tmp_path / "artifacts"),
        "status",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "next_required_step" in out

