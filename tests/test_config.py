from pathlib import Path

import pytest

from bioenv.cli import main
from bioenv.config import ConfigError, load_config


EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "tools.example.yaml"


def test_example_catalog():
    config = load_config(EXAMPLE)
    assert config["tools"]["bakta"]["environment"]["BAKTA_DB"].endswith("/db")
    assert "kraken2-build" in config["tools"]["kraken2"]["commands"]


def test_missing_package_is_rejected(tmp_path):
    config_file = tmp_path / "bad.yaml"
    config_file.write_text("tools:\n  foo:\n    commands: [foo]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="package"):
        load_config(config_file)


def test_cli_validation(capsys):
    assert main(["--config", str(EXAMPLE), "validate"]) == 0
    assert "3 tool(s)" in capsys.readouterr().out
