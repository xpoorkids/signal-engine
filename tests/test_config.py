from __future__ import annotations

import importlib
import sys
from pathlib import Path

from dotenv import dotenv_values


def test_settings_load_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WATCH_LOG_PATH", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ALERT_COOLDOWN_MIN", raising=False)
    (tmp_path / ".env").write_text(
        "WATCH_LOG_PATH=tmp/watch.log\nENV=staging\nALERT_COOLDOWN_MIN=30\n",
        encoding="utf-8",
    )

    sys.modules.pop("app.config", None)
    config = importlib.import_module("app.config")

    assert config.settings.WATCH_LOG_PATH == "tmp/watch.log"
    assert config.settings.ENV == "staging"
    assert config.settings.ALERT_COOLDOWN_MIN == 30
def test_env_example_parses_x_query_template():
    project_root = Path(__file__).resolve().parents[1]
    parsed = dotenv_values(project_root / ".env.example", interpolate=False)

    assert parsed["X_QUERY_TEMPLATE"] == '"{name}" OR "${symbol}" -is:retweet -is:reply lang:en'
