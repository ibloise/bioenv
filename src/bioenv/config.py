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
SETTING_KEYS = {*DEFAULT_SETTINGS, 'databases_root'}
TOOL_KEYS = {'package', 'python', 'version', 'commands', 'environment', 'databases'}
DATABASE_KEYS = {'name', 'path', 'required'}


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys instead of hiding typos."""


def _construct_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConfigError('YAML mapping keys must be scalar values') from exc
        if duplicate:
            raise ConfigError(f'Duplicate YAML key: {key!r}')
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


class ConfigError(ValueError):
    """Invalid or unreadable bioenv configuration."""


def _absolute_path(value, label):
    if not isinstance(value, str):
        raise ConfigError(f'{label} must be an absolute path without ..')
    try:
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f'{label} must be a valid absolute path') from exc
    if not path.is_absolute() or '..' in path.parts:
        raise ConfigError(f'{label} must be an absolute path without ..')
    return path


def load_config(path: Path) -> dict:
    try:
        with path.open(encoding='utf-8') as stream:
            config = yaml.load(stream, Loader=_UniqueKeyLoader)
    except OSError as exc:
        raise ConfigError(f'Cannot read config {path}: {exc}') from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f'Invalid YAML in {path}: {exc}') from exc

    if not isinstance(config, dict) or not isinstance(config.get('tools'), dict):
        raise ConfigError("Configuration must contain a 'tools' mapping")
    unknown_root_keys = set(config) - {'settings', 'tools'}
    if unknown_root_keys:
        names = ', '.join(sorted(map(str, unknown_root_keys)))
        raise ConfigError(f'Unknown top-level configuration key(s): {names}')
    settings = config.setdefault('settings', {})
    if not isinstance(settings, dict):
        raise ConfigError('settings must be a mapping')
    unknown_settings = set(settings) - SETTING_KEYS
    if unknown_settings:
        names = ', '.join(sorted(map(str, unknown_settings)))
        raise ConfigError(f'Unknown setting(s): {names}')
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
        unknown_tool_keys = set(spec) - TOOL_KEYS
        if unknown_tool_keys:
            raise ConfigError(
                f'Tool {name!r}: unknown setting(s): {", ".join(sorted(map(str, unknown_tool_keys)))}'
            )
        if not isinstance(spec.get('package'), str) or not NAME.fullmatch(spec['package']):
            raise ConfigError(f'Tool {name!r} needs a simple package name')
        if spec['package'] in ('.', '..'):
            raise ConfigError(f'Tool {name!r} package must be a simple package name')
        for key in ('python', 'version'):
            if key in spec and (not isinstance(spec[key], str) or not spec[key]):
                raise ConfigError(f'Tool {name!r}: {key} must be a quoted string')
        commands = spec.get('commands')
        if not isinstance(commands, list) or not commands or any(
            not isinstance(c, str) or not NAME.fullmatch(c) or c in ('.', '..') for c in commands
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
            unknown_db_keys = set(db) - DATABASE_KEYS
            if unknown_db_keys:
                raise ConfigError(
                    f'Tool {name!r} database: unknown setting(s): '
                    f'{", ".join(sorted(map(str, unknown_db_keys)))}'
                )
            if not db['name']:
                raise ConfigError(f'Tool {name!r}: database name must not be empty')
            _absolute_path(db.get('path'), f'Tool {name!r} database path')
    return config
