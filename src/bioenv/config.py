"""Load and validate the small bioenv YAML catalog."""

import re
from pathlib import Path

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
TOOL_KEYS = {
    'package', 'python', 'version', 'commands', 'environment', 'databases', 'categories',
}
DATABASE_KEYS = {'name', 'path', 'env', 'required', 'type'}


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


def _read_yaml(path: Path):
    try:
        with path.open(encoding='utf-8') as stream:
            config = yaml.load(stream, Loader=_UniqueKeyLoader)
    except OSError as exc:
        raise ConfigError(f'Cannot read config {path}: {exc}') from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f'Invalid YAML in {path}: {exc}') from exc
    return config


def _require_mapping(value, label):
    if not isinstance(value, dict):
        raise ConfigError(f'{label} must be a mapping')
    return value


def _reject_unknown_keys(mapping, allowed, label):
    unknown = set(mapping) - allowed
    if unknown:
        names = ', '.join(sorted(map(str, unknown)))
        raise ConfigError(f'Unknown {label}: {names}')


def _validate_catalog_shape(config):
    if not isinstance(config, dict) or not isinstance(config.get('tools'), dict):
        raise ConfigError("Configuration must contain a 'tools' mapping")
    _reject_unknown_keys(config, {'settings', 'tools'}, 'top-level configuration key(s)')


def _validated_settings(value):
    settings = dict(_require_mapping(value, 'settings'))
    _reject_unknown_keys(settings, SETTING_KEYS, 'setting(s)')
    settings = {**DEFAULT_SETTINGS, **settings}
    for key, setting in settings.items():
        _absolute_path(setting, f'settings.{key}')
    return settings


def _validate_commands(name, value, claimed_commands):
    if isinstance(value, list):
        if not value or any(not isinstance(command, str) for command in value) \
                or len(set(value)) != len(value):
            raise ConfigError(f'Tool {name!r} needs unique, non-empty commands')
        commands = {command: command for command in value}
    elif isinstance(value, dict) and value:
        commands = value
    else:
        raise ConfigError(f'Tool {name!r} needs a non-empty list or mapping of commands')
    for public, executable in commands.items():
        for command in (public, executable):
            if not isinstance(command, str) or not NAME.fullmatch(command) or command in ('.', '..'):
                raise ConfigError(
                    f'Tool {name!r} commands must be simple names inside environment bin'
                )
    for command in commands:
        if command in claimed_commands:
            raise ConfigError(
                f'Command {command!r} belongs to both {claimed_commands[command]} and {name}'
            )
        claimed_commands[command] = name


def _validate_environment(name, spec):
    environment = spec.get('environment', {})
    if not isinstance(environment, dict) or any(
        not isinstance(key, str)
        or not ENV_NAME.fullmatch(key)
        or not isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ConfigError(f'Tool {name!r}: environment must map names to strings')


def _validate_database(name, database, databases_root):
    _reject_unknown_keys(database, DATABASE_KEYS, f'Tool {name!r} database setting(s)')
    database_name = database.get('name')
    if not isinstance(database_name, str) or not NAME.fullmatch(database_name):
        raise ConfigError(f'Tool {name!r}: database name must be a simple name')
    path_value = database.get('path')
    if not isinstance(path_value, str) or not path_value:
        raise ConfigError(f'Tool {name!r} database {database_name!r}: path must be a string')
    path = Path(path_value)
    if path.is_absolute():
        _absolute_path(path_value, f'Tool {name!r} database path')
    elif path == Path('.') or '..' in path.parts or databases_root is None:
        raise ConfigError(
            f'Tool {name!r} database {database_name!r}: relative path needs databases_root '
            'and must not contain ..'
        )
    if 'env' in database and (
        not isinstance(database['env'], str) or not ENV_NAME.fullmatch(database['env'])
    ):
        raise ConfigError(f'Tool {name!r} database {database_name!r}: invalid env variable')
    if database.get('required', False) is not True and database.get('required', False) is not False:
        raise ConfigError(f'Tool {name!r} database {database_name!r}: required must be boolean')
    if database.get('type', 'directory') not in ('directory', 'file'):
        raise ConfigError(
            f'Tool {name!r} database {database_name!r}: type must be directory or file'
        )


def _validate_databases(name, value, databases_root, environment):
    databases = value.get('databases', [])
    if not isinstance(databases, list) or any(
        not isinstance(database, dict)
        or not isinstance(database.get('name'), str)
        or not isinstance(database.get('required', False), bool)
        for database in databases
    ):
        raise ConfigError(f'Tool {name!r}: invalid databases list')
    names = set()
    env_names = set(environment)
    for database in databases:
        _validate_database(name, database, databases_root)
        if database['name'] in names:
            raise ConfigError(f'Tool {name!r}: duplicate database name {database["name"]!r}')
        names.add(database['name'])
        env_name = database.get('env')
        if env_name is not None:
            if env_name in env_names:
                raise ConfigError(
                    f'Tool {name!r}: duplicate environment variable {env_name!r} '
                    'in environment and databases'
                )
            env_names.add(env_name)


def _validate_tool(name, value, claimed_commands, databases_root):
    if not isinstance(name, str) or not NAME.fullmatch(name) or name in ('.', '..'):
        raise ConfigError(f'Invalid tool name: {name!r}')
    spec = _require_mapping(value, f'Tool {name!r}')
    _reject_unknown_keys(spec, TOOL_KEYS, f'Tool {name!r} setting(s)')

    package = spec.get('package')
    if not isinstance(package, str) or not NAME.fullmatch(package) or package in ('.', '..'):
        raise ConfigError(f'Tool {name!r} needs a simple package name')
    for key in ('python', 'version'):
        if key in spec and (not isinstance(spec[key], str) or not spec[key]):
            raise ConfigError(f'Tool {name!r}: {key} must be a quoted string')
    categories = spec.get('categories', [])
    if not isinstance(categories, list) or any(
        not isinstance(category, str) or not category.strip() for category in categories
    ):
        raise ConfigError(f'Tool {name!r}: categories must be a list of non-empty strings')
    if len(set(categories)) != len(categories):
        raise ConfigError(f'Tool {name!r}: duplicate category')

    _validate_commands(name, spec.get('commands'), claimed_commands)
    environment = spec.get('environment', {})
    _validate_environment(name, spec)
    _validate_databases(name, spec, databases_root, environment)


def _validate_tools(tools, databases_root):
    claimed_commands = {}
    for name, spec in tools.items():
        _validate_tool(name, spec, claimed_commands, databases_root)


def load_config(path: Path) -> dict:
    config = _read_yaml(path)
    _validate_catalog_shape(config)
    config['settings'] = _validated_settings(config.get('settings', {}))
    _validate_tools(config['tools'], config['settings'].get('databases_root'))
    return config
