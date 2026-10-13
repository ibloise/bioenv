"""Load and validate the small bioenv YAML catalog."""

from pathlib import Path
import re
import yaml

DEFAULT_CONFIG = Path('/etc/bioenv/tools.yaml')
NAME = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')
ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
DEFAULT_SETTINGS = {
    'envs_root': '/opt/bioinformatics/envs',
    'bin_dir': '/usr/local/bin',
    'micromamba': '/usr/local/bin/micromamba',
    'mamba_root': '/opt/bioinformatics',
}


class ConfigError(ValueError):
    """Invalid or unreadable bioenv configuration."""


def _absolute_path(value, label):
    if not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts:
        raise ConfigError(f'{label} must be an absolute path without ..')
    return Path(value)


def load_config(path: Path) -> dict:
    try:
        with path.open(encoding='utf-8') as stream:
            config = yaml.safe_load(stream)
    except OSError as exc:
        raise ConfigError(f'Cannot read config {path}: {exc}') from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f'Invalid YAML in {path}: {exc}') from exc

    if not isinstance(config, dict) or not isinstance(config.get('tools'), dict):
        raise ConfigError("Configuration must contain a 'tools' mapping")
    settings = config.setdefault('settings', {})
    if not isinstance(settings, dict):
        raise ConfigError('settings must be a mapping')
    for key, default in DEFAULT_SETTINGS.items():
        settings.setdefault(key, default)
        _absolute_path(settings[key], f'settings.{key}')
    if 'databases_root' in settings:
        _absolute_path(settings['databases_root'], 'settings.databases_root')

    all_commands = {}
    for name, spec in config['tools'].items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in ('.', '..'):
            raise ConfigError(f'Invalid tool name: {name!r}')
        if not isinstance(spec, dict):
            raise ConfigError(f'Tool {name!r} must be a mapping')
        if not isinstance(spec.get('package'), str) or not NAME.fullmatch(spec['package']):
            raise ConfigError(f'Tool {name!r} needs a simple package name')
        for key in ('python', 'version'):
            if key in spec and (not isinstance(spec[key], str) or not spec[key]):
                raise ConfigError(f'Tool {name!r}: {key} must be a quoted string')
        commands = spec.get('commands')
        if not isinstance(commands, list) or not commands or any(
            not isinstance(c, str) or not NAME.fullmatch(c) for c in commands
        ) or len(set(commands)) != len(commands):
            raise ConfigError(f'Tool {name!r} needs a unique, non-empty list of commands')
        for command in commands:
            if command in all_commands:
                raise ConfigError(f'Command {command!r} belongs to both {all_commands[command]} and {name}')
            all_commands[command] = name
        env = spec.get('environment', {})
        if not isinstance(env, dict) or any(
            not isinstance(k, str) or not ENV_NAME.fullmatch(k) or not isinstance(v, str)
            for k, v in env.items()
        ):
            raise ConfigError(f'Tool {name!r}: environment must map names to strings')
        databases = spec.get('databases', [])
        if not isinstance(databases, list) or any(
            not isinstance(db, dict) or not isinstance(db.get('name'), str)
            or not isinstance(db.get('required', False), bool)
            for db in databases
        ):
            raise ConfigError(f'Tool {name!r}: invalid databases list')
        for db in databases:
            _absolute_path(db.get('path'), f'Tool {name!r} database path')
    return config
