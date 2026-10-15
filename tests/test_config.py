from pathlib import Path

import pytest

from bioenv.cli import main
from bioenv.config import ConfigError, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "tools.example.yaml"


def test_example_catalog():
    config = load_config(EXAMPLE)
    assert config["tools"]["bakta"]["environment"]["BAKTA_DB"].endswith("/db")
    assert "kraken2-build" in config["tools"]["kraken2"]["commands"]
    assert config["tools"]["spades"]["commands"] == {
        "spades": "spades.py", "spades.py": "spades.py",
    }


def test_alias_mapping_is_accepted_and_legacy_list_is_preserved(tmp_path):
    config_file = tmp_path / 'aliases.yaml'
    config_file.write_text(
        'tools:\n  foo:\n    package: foo\n    commands:\n'
        '      spades: spades.py\n      spades.py: spades.py\n'
        '  bar:\n    package: bar\n    commands: [fastqc]\n', encoding='utf-8'
    )
    config = load_config(config_file)
    assert config['tools']['foo']['commands']['spades'] == 'spades.py'
    assert config['tools']['bar']['commands'] == ['fastqc']


@pytest.mark.parametrize('executable', ['/tmp/outside', '../outside', 'subdir/tool'])
def test_alias_executable_cannot_escape_environment_bin(tmp_path, executable):
    config_file = tmp_path / 'bad-alias.yaml'
    config_file.write_text(
        f'tools:\n  foo:\n    package: foo\n    commands: {{public: "{executable}"}}\n',
        encoding='utf-8',
    )
    with pytest.raises(ConfigError, match='simple names inside environment bin'):
        load_config(config_file)


def test_missing_package_is_rejected(tmp_path):
    config_file = tmp_path / "bad.yaml"
    config_file.write_text("tools:\n  foo:\n    commands: [foo]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="package"):
        load_config(config_file)


def test_cli_validation(capsys):
    assert main(["--config", str(EXAMPLE), "validate"]) == 0
    assert "3 tool(s)" in capsys.readouterr().out


def test_bioenv_config_environment_variable_sets_default_catalog(monkeypatch, capsys):
    monkeypatch.setenv('BIOENV_CONFIG', str(EXAMPLE))
    assert main(['validate']) == 0
    assert 'OK:' in capsys.readouterr().out


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    config_file = tmp_path / "duplicate.yaml"
    config_file.write_text("tools:\n  foo:\n    package: first\n    package: second\n    commands: [foo]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Duplicate YAML key.*package"):
        load_config(config_file)


@pytest.mark.parametrize("extra", ["typo: true", "settings:\n  env_root: /tmp/envs"])
def test_unknown_configuration_keys_are_reported(tmp_path, extra):
    config_file = tmp_path / "unknown.yaml"
    prefix = "tools:\n  foo:\n    package: foo\n    commands: [foo]\n"
    config_file.write_text(f"{extra}\n{prefix}", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown"):
        load_config(config_file)


@pytest.mark.parametrize("command", [".", ".."])
def test_dot_commands_are_rejected(tmp_path, command):
    config_file = tmp_path / "bad-command.yaml"
    config_file.write_text(
        f"tools:\n  foo:\n    package: foo\n    commands: ['{command}']\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="commands"):
        load_config(config_file)
