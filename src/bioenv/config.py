"""Minimal configuration loading; installation logic will be added later."""

from pathlib import Path

import yaml

DEFAULT_CONFIG = Path("/etc/bioenv/tools.yaml")


class ConfigError(ValueError):
    """Invalid or unreadable bioenv configuration."""


def load_config(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
    except OSError as exc:
        raise ConfigError(f"Cannot read config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(config, dict) or not isinstance(config.get("tools"), dict):
        raise ConfigError("Configuration must contain a 'tools' mapping")
    for name, spec in config["tools"].items():
        if not isinstance(name, str) or not name or not isinstance(spec, dict):
            raise ConfigError("Each tool must have a name and a mapping of settings")
        if not isinstance(spec.get("package"), str) or not spec["package"]:
            raise ConfigError(f"Tool {name!r} needs a package name")
        commands = spec.get("commands")
        if not isinstance(commands, list) or not commands or not all(
            isinstance(command, str) and command for command in commands
        ):
            raise ConfigError(f"Tool {name!r} needs a non-empty list of commands")
    return config
