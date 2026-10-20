"""Manage stable Micromamba prefixes and explicitly declared command launchers."""

import fcntl
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

MARKER = '# Managed by bioenv; tool='
SHELL_SHEBANG = '#!/bin/sh'
ENVIRONMENT_BIN = 'bin'
MICROMAMBA_ROOT_ENV = 'MAMBA_ROOT_PREFIX'
CREATE_COMMAND = 'create'
UPDATE_COMMAND = 'update'
RUN_COMMAND = 'run'
PREFIX_OPTION = '-p'
YES_OPTION = '-y'
ENV_REMOVE_COMMAND = 'env'
REMOVE_COMMAND = 'remove'
PYTHON_PACKAGE = 'python'


class BioenvError(RuntimeError):
    """Unsafe operation or external command failure."""


def paths(config, tool):
    settings = config['settings']
    return (Path(settings['envs_root']) / tool, Path(settings['bin_dir']))


def command_map(spec):
    """Return public launcher names mapped to executable names in environment/bin."""
    commands = spec['commands']
    return commands if isinstance(commands, dict) else {command: command for command in commands}


def _database_specs(config, tool):
    if tool not in config['tools']:
        raise BioenvError(f'Unknown tool: {tool}')
    return config['tools'][tool].get('databases', [])


def _database_spec(config, tool, database_name):
    for database in _database_specs(config, tool):
        if database['name'] == database_name:
            return database
    raise BioenvError(f'Unknown database {database_name!r} for tool {tool!r}')


def _resolved_database_path(config, database):
    configured = Path(database['path'])
    if configured.is_absolute():
        return configured
    root_value = config['settings'].get('databases_root')
    if root_value is None:
        raise BioenvError('Relative database paths require settings.databases_root')
    root = Path(root_value)
    try:
        resolved_root = root.resolve(strict=False)
        resolved_path = (root / configured).resolve(strict=False)
        resolved_path.relative_to(resolved_root)
    except BioenvError:
        raise
    except ValueError as exc:
        raise BioenvError(
            f'Relative database path escapes settings.databases_root: {configured}'
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise BioenvError(f'Cannot safely resolve database path {configured}: {exc}') from exc
    return resolved_path


def _managed_database_path(config, database):
    root_value = config['settings'].get('databases_root')
    if root_value is None:
        raise BioenvError('Database import requires settings.databases_root')
    try:
        root = Path(root_value).resolve(strict=False)
        path = _resolved_database_path(config, database).resolve(strict=False)
        if not path.relative_to(root).parts:
            raise BioenvError(
                'Database destination cannot be settings.databases_root itself'
            )
    except BioenvError:
        raise
    except ValueError as exc:
        raise BioenvError(
            f'Database destination must stay within settings.databases_root: '
            f'{database["path"]}'
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise BioenvError(f'Cannot safely resolve database destination: {exc}') from exc
    return root, path


def _wrapper(tool, command, executable, config):
    spec = config['tools'][tool]
    prefix, _ = paths(config, tool)
    micromamba = config['settings']['micromamba']
    lines = [SHELL_SHEBANG, f'{MARKER}{tool}']
    for key, value in spec.get('environment', {}).items():
        lines.append(f'export {key}={shlex.quote(value)}')
    for database in spec.get('databases', []):
        if 'env' in database:
            value = str(_resolved_database_path(config, database))
            lines.append(f'export {database["env"]}={shlex.quote(value)}')
    lines.append(
        f'export {MICROMAMBA_ROOT_ENV}={shlex.quote(config["settings"]["mamba_root"])}'
    )
    executable_path = prefix / ENVIRONMENT_BIN / executable
    lines.append(f'exec {shlex.quote(micromamba)} run -p {shlex.quote(str(prefix))} '
                 f'{shlex.quote(str(executable_path))} "$@"')
    return '\n'.join(lines) + '\n'


def _owned(path, tool):
    if path.is_symlink() or not path.is_file():
        return False
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
        return len(lines) >= 2 and lines[0] == SHELL_SHEBANG and lines[1] == MARKER + tool
    except (OSError, UnicodeError, IndexError):
        return False


def _current_launcher(path, tool, command, executable, config):
    if not _owned(path, tool) or not os.access(path, os.X_OK):
        return False
    try:
        return path.read_text(encoding='utf-8') == _wrapper(tool, command, executable, config)
    except (OSError, UnicodeError):
        return False


def _is_executable(path):
    return path.is_file() and os.access(path, os.X_OK)


def _preflight(config, tool):
    prefix, bin_dir = paths(config, tool)
    if prefix.is_symlink():
        raise BioenvError(f'Refusing to use environment path because it is a symlink: {prefix}')
    if bin_dir.is_symlink():
        raise BioenvError(f'Refusing to publish launchers through a symlink directory: {bin_dir}')
    for command in command_map(config['tools'][tool]):
        dest = bin_dir / command
        if (dest.exists() or dest.is_symlink()) and not _owned(dest, tool):
            raise BioenvError(
                f'Refusing to replace existing command because it is not a bioenv launcher: {dest}'
            )
    return prefix, bin_dir


def plan(config, tool):
    prefix, bin_dir = _preflight(config, tool)
    spec = config['tools'][tool]
    packages = []
    if 'python' in spec:
        packages.append(f"{PYTHON_PACKAGE}={spec['python']}")
    packages.append(spec['package'] + ('=' + spec['version'] if 'version' in spec else ''))
    command = [config['settings']['micromamba'], CREATE_COMMAND, PREFIX_OPTION,
               str(prefix), *packages, YES_OPTION]
    return command, [str(bin_dir / c) for c in command_map(spec)]


def plan_update(config, tool, latest=False):
    """Build an in-place Micromamba update command and launcher destinations."""
    prefix, bin_dir = _preflight(config, tool)
    spec = config['tools'][tool]
    packages = []
    if 'python' in spec:
        packages.append(f"{PYTHON_PACKAGE}={spec['python']}")
    package = spec['package']
    if not latest and 'version' in spec:
        package += '=' + spec['version']
    packages.append(package)
    command = [config['settings']['micromamba'], UPDATE_COMMAND, PREFIX_OPTION,
               str(prefix), *packages, YES_OPTION]
    return command, [str(bin_dir / c) for c in command_map(spec)]


@contextmanager
def _environment_lock(config, tool):
    """Coordinate bioenv mutations of one prefix across processes."""
    root = Path(config['settings']['envs_root'])
    lock_dir = root / '.bioenv-locks'
    lock_path = lock_dir / f'{tool}.lock'
    if root.is_symlink() or lock_dir.is_symlink() or lock_path.is_symlink():
        raise BioenvError(
            f'Refusing to use environment or lock path through a symlink: {lock_path}'
        )
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BioenvError(f'Cannot lock environment for {tool}: {exc}') from exc
    try:
        stream = lock_path.open('a', encoding='utf-8')
    except OSError as exc:
        raise BioenvError(f'Cannot lock environment for {tool}: {exc}') from exc
    with stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise BioenvError(f'Cannot lock environment for {tool}: {exc}') from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _conda_package_version(prefix, package):
    metadata = prefix / 'conda-meta'
    if not metadata.is_dir() or not (metadata / 'history').is_file():
        raise BioenvError(
            f'Environment for package {package!r} is not a valid Conda environment: {prefix}'
        )
    for record in metadata.glob('*.json'):
        try:
            data = json.loads(record.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if str(data.get('name', '')).casefold() == package.casefold():
            return str(data.get('version', '')) or None
    raise BioenvError(
        f'Package {package!r} is not installed in environment {prefix}. '
        'bioenv update requires an existing installation; use "bioenv install" first.'
    )


def _update_preconditions(config, tool):
    envs_root = Path(config['settings']['envs_root'])
    if envs_root.is_symlink():
        raise BioenvError(f'Refusing to use environment root because it is a symlink: {envs_root}')
    prefix, _ = _preflight(config, tool)
    if prefix.is_symlink():
        raise BioenvError(f'Refusing to use environment path because it is a symlink: {prefix}')
    if not prefix.is_dir():
        raise BioenvError(
            f'Environment for {tool} does not exist at {prefix}; use "bioenv install {tool}".'
        )
    package = config['tools'][tool]['package']
    version = _conda_package_version(prefix, package)
    return prefix, version


_CRITICAL_PATHS = tuple(Path(path) for path in (
    '/', '/bin', '/boot', '/dev', '/etc', '/home', '/lib', '/lib64', '/opt',
    '/proc', '/root', '/run', '/sbin', '/srv', '/sys', '/usr', '/var', '/tmp',
))


def _remove_preconditions(config, tool, launchers_only=False, allow_conflicts=False):
    if tool not in config['tools']:
        raise BioenvError(f'Unknown tool: {tool}')
    prefix, bin_dir = paths(config, tool)
    envs_root = Path(config['settings']['envs_root'])
    if not envs_root.is_absolute() or '..' in envs_root.parts:
        raise BioenvError(f'Refusing unsafe environment root: {envs_root}')
    if any(part.is_symlink() for part in (envs_root, *envs_root.parents)) \
            or prefix.is_symlink():
        raise BioenvError(f'Refusing to remove an environment through a symlink: {prefix}')
    try:
        root_real = envs_root.resolve(strict=True)
        prefix_real = prefix.resolve(strict=True)
    except FileNotFoundError:
        root_real = envs_root.resolve(strict=False)
        prefix_real = (root_real / tool)
    except (OSError, RuntimeError) as exc:
        raise BioenvError(f'Cannot safely resolve environment path {prefix}: {exc}') from exc
    if prefix_real.parent != root_real or prefix_real.name != tool:
        raise BioenvError(f'Refusing environment path outside configured root: {prefix}')
    for critical in _CRITICAL_PATHS:
        if prefix_real == critical or prefix_real in critical.parents:
            raise BioenvError(f'Refusing to remove critical system directory: {prefix_real}')
    protected = []
    for key in ('mamba_root', 'databases_root', 'bin_dir'):
        value = config['settings'].get(key)
        if value is not None:
            candidate = Path(value)
            if not candidate.is_absolute() or '..' in candidate.parts:
                raise BioenvError(f'Refusing unsafe configured {key}: {candidate}')
            try:
                resolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise BioenvError(f'Cannot safely resolve configured {key} {candidate}: {exc}') \
                    from exc
            protected.append((key, resolved))
    for key, candidate in protected:
        overlap = prefix_real == candidate or prefix_real in candidate.parents
        if key in ('databases_root', 'bin_dir'):
            overlap = overlap or candidate in prefix_real.parents
        if overlap:
            raise BioenvError(
                f'Refusing to remove {prefix_real}; it overlaps configured {key}: {candidate}'
            )
    if not launchers_only and prefix.exists():
        if not prefix.is_dir():
            raise BioenvError(f'Environment path is not a directory: {prefix}')
        if not (prefix / 'conda-meta' / 'history').is_file():
            raise BioenvError(f'Environment is not a valid Conda environment: {prefix}')
    if any(part.is_symlink() for part in (bin_dir, *bin_dir.parents)):
        raise BioenvError(f'Refusing to inspect launchers through a symlink directory: {bin_dir}')
    launchers = []
    for public in command_map(config['tools'][tool]):
        path = bin_dir / public
        if path.is_symlink():
            launchers.append((path, 'conflict: symlink'))
        elif not path.exists():
            launchers.append((path, 'missing'))
        elif not _owned(path, tool):
            launchers.append((path, 'conflict: not managed by this tool'))
        elif not _current_launcher(
            path, tool, public, command_map(config['tools'][tool])[public], config
        ):
            launchers.append((path, 'conflict: modified launcher'))
        else:
            launchers.append((path, 'managed'))
    conflicts = [(path, state) for path, state in launchers if state.startswith('conflict:')]
    if conflicts and not allow_conflicts:
        detail = '; '.join(f'{path} ({state.removeprefix("conflict: ")})'
                           for path, state in conflicts)
        raise BioenvError(f'Launcher conflict prevents removal: {detail}')
    command = [config['settings']['micromamba'], ENV_REMOVE_COMMAND, REMOVE_COMMAND,
               PREFIX_OPTION, str(prefix), YES_OPTION]
    return prefix, bin_dir, command, launchers


def remove_plan(config, tool, launchers_only=False):
    """Return a validated, non-mutating removal plan for CLI display and confirmation."""
    prefix, _, command, launchers = _remove_preconditions(
        config, tool, launchers_only=launchers_only, allow_conflicts=True
    )
    exists = prefix.is_dir()
    return {
        'tool': tool,
        'prefix': prefix,
        'command': None if launchers_only or not exists else command,
        'launchers': launchers,
        'environment_exists': exists,
        'launchers_only': launchers_only,
        'blocked': [(path, state) for path, state in launchers
                    if state.startswith('conflict:')],
    }


def remove(config, tool, launchers_only=False, dry_run=False):
    """Remove a declared environment through Micromamba and its current launchers."""
    if dry_run:
        return remove_plan(config, tool, launchers_only=launchers_only)
    initial = remove_plan(config, tool, launchers_only=launchers_only)
    if initial['blocked']:
        detail = '; '.join(f'{path} ({state.removeprefix("conflict: ")})'
                           for path, state in initial['blocked'])
        raise BioenvError(f'Launcher conflict prevents removal: {detail}')
    if not launchers_only and not initial['environment_exists']:
        return initial
    with _environment_lock(config, tool):
        prefix, _, command, launchers = _remove_preconditions(
            config, tool, launchers_only=launchers_only
        )
        exists = prefix.is_dir()
        if not launchers_only and not exists:
            return {
                'tool': tool, 'prefix': prefix, 'command': command, 'launchers': launchers,
                'environment_exists': False, 'launchers_only': False,
            }
        if not launchers_only:
            env = os.environ.copy()
            env[MICROMAMBA_ROOT_ENV] = config['settings']['mamba_root']
            try:
                subprocess.run(command, env=env, check=True)
            except subprocess.CalledProcessError as exc:
                raise BioenvError(
                    f'Micromamba failed to remove {tool!r} (exit status {exc.returncode}); '
                    f'the environment may require inspection at {prefix}. Launchers were kept.'
                ) from exc
            except KeyboardInterrupt as exc:
                raise BioenvError(
                    f'Micromamba removal of {tool!r} was interrupted; the environment may '
                    f'require inspection at {prefix}. Launchers were kept.'
                ) from exc
            except OSError as exc:
                raise BioenvError(
                    f'Could not run Micromamba at {config["settings"]["micromamba"]}: {exc}; '
                    f'the environment may require inspection at {prefix}. Launchers were kept.'
                ) from exc
            if prefix.exists():
                raise BioenvError(
                    f'Micromamba reported success but the environment still exists at {prefix}; '
                    'launchers were kept. Inspect the environment before retrying.'
                )
        pending = []
        command_map_for_tool = command_map(config['tools'][tool])
        for path, state in launchers:
            if state != 'managed':
                continue
            executable = command_map_for_tool[path.name]
            if path.is_symlink() or not _current_launcher(
                path, tool, path.name, executable, config
            ):
                pending.append(f'{path}: launcher changed after validation')
                continue
            try:
                path.unlink()
            except OSError as exc:
                pending.append(f'{path}: {exc}')
            except KeyboardInterrupt:
                pending.extend(
                    f'{remaining}: removal interrupted'
                    for remaining, remaining_state in launchers
                    if remaining_state == 'managed' and remaining.exists()
                )
                break
        if pending:
            raise BioenvError(
                f'Environment removal completed for {tool}, but launcher cleanup failed; '
                f'resources pending removal: {"; ".join(pending)}'
            )
        return {
            'tool': tool, 'prefix': prefix, 'command': command,
            'launchers': launchers, 'environment_exists': exists,
            'launchers_only': launchers_only,
        }


def update(config, tool, latest=False, dry_run=False):
    """Update a declared package in its existing Conda prefix, then sync launchers."""
    if tool not in config['tools']:
        raise BioenvError(f'Unknown tool: {tool}')

    def execute():
        prefix, old_version = _update_preconditions(config, tool)
        command, launchers = plan_update(config, tool, latest=latest)
        env = os.environ.copy()
        env[MICROMAMBA_ROOT_ENV] = config['settings']['mamba_root']
        run_command = [*command, '--dry-run'] if dry_run else command
        try:
            subprocess.run(run_command, env=env, check=True)
        except subprocess.CalledProcessError as exc:
            action = 'resolve update for' if dry_run else 'update'
            raise BioenvError(
                f'Micromamba failed to {action} {tool!r} (exit status {exc.returncode}); '
                f'the environment may be unchanged or partially updated at {prefix}.'
            ) from exc
        except OSError as exc:
            raise BioenvError(
                f'Could not run Micromamba at {config["settings"]["micromamba"]}: {exc}'
            ) from exc
        if dry_run:
            return command, launchers, old_version, None
        new_version = _conda_package_version(prefix, config['tools'][tool]['package'])
        missing = [name for name, executable in command_map(config['tools'][tool]).items()
                   if not _is_executable(prefix / ENVIRONMENT_BIN / executable)]
        if missing:
            raise BioenvError(
                f'Update completed, but environment for {tool} is missing executable(s): '
                f'{", ".join(missing)}. Launchers were not published.'
            )
        try:
            _publish(config, tool)
        except BioenvError as exc:
            raise BioenvError(
                f'Environment for {tool} was updated at {prefix}, but launcher publication '
                f'failed: {exc}. The environment is not rolled back.'
            ) from exc
        return command, launchers, old_version, new_version

    if dry_run:
        return execute()
    with _environment_lock(config, tool):
        return execute()


def install(config, tool, dry_run=False):
    if not dry_run:
        with _environment_lock(config, tool):
            return _install(config, tool, dry_run=False)
    return _install(config, tool, dry_run=True)


def _install(config, tool, dry_run=False):
    prefix, _ = _preflight(config, tool)
    if prefix.exists():
        raise BioenvError(
            f'Environment path already exists: {prefix}. Use "bioenv check {tool}" to inspect it; '
            f'use "bioenv publish {tool}" only if it is the intended environment.'
        )
    command, launchers = plan(config, tool)
    if dry_run:
        return command, launchers
    env = os.environ.copy()
    env[MICROMAMBA_ROOT_ENV] = config['settings']['mamba_root']
    try:
        subprocess.run(command, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        raise BioenvError(
            f'Micromamba failed to create {tool!r} (exit status {exc.returncode}); '
            f'the target may be incomplete at {prefix}. Inspect it before retrying.'
        ) from exc
    except OSError as exc:
        raise BioenvError(
            f'Could not run Micromamba at {config["settings"]["micromamba"]}: {exc}'
        ) from exc
    try:
        _publish(config, tool)
    except BioenvError as exc:
        raise BioenvError(
            f'Environment for {tool} was created at {prefix}, but launcher publication failed: '
            f'{exc}. Fix the issue and run "bioenv publish {tool}" to finish.'
        ) from exc
    return command, launchers


def publish(config, tool, dry_run=False):
    if not dry_run:
        with _environment_lock(config, tool):
            return _publish(config, tool, dry_run=False)
    return _publish(config, tool, dry_run=True)


def _publish(config, tool, dry_run=False):
    prefix, bin_dir = _preflight(config, tool)
    if not prefix.is_dir():
        raise BioenvError(f'Environment for {tool} is missing or is not a directory: {prefix}')
    commands = command_map(config['tools'][tool])
    missing = [public for public, executable in commands.items()
               if not _is_executable(prefix / ENVIRONMENT_BIN / executable)]
    if missing:
        raise BioenvError(
            f'Environment for {tool} is missing executable(s): {", ".join(missing)}. '
            'Check the package contents or correct the catalog commands.'
        )
    if dry_run:
        return [bin_dir / c for c in commands]
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BioenvError(f'Cannot create launcher directory {bin_dir}: {exc}') from exc
    # Only replace files carrying this tool's ownership marker.
    for command, executable in commands.items():
        dest = bin_dir / command
        content = _wrapper(tool, command, executable, config)
        if dest.exists() or dest.is_symlink():
            if not _owned(dest, tool):
                raise BioenvError(f'Refusing to replace command that is not managed by bioenv: {dest}')
            _write_launcher(dest, content, replace=True)
        else:
            _write_launcher(dest, content, replace=False)
    return [bin_dir / c for c in commands]


def _write_launcher(dest, content, replace):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=dest.parent,
            prefix=f'.{dest.name}.bioenv-', delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
        temp_path.chmod(0o755)
        if replace:
            os.replace(temp_path, dest)
        else:
            os.link(temp_path, dest)
            temp_path.unlink()
    except FileExistsError as exc:
        raise BioenvError(
            f'Refusing to replace launcher created by another process: {dest}'
        ) from exc
    except OSError as exc:
        action = 'update' if replace else 'create'
        raise BioenvError(f'Cannot {action} launcher {dest}: {exc}') from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def check(config, tool):
    prefix, bin_dir = paths(config, tool)
    result = []
    environment_ok = prefix.is_dir() and not prefix.is_symlink()
    result.append((environment_ok, f'Environment directory: {prefix}'))
    for command, executable in command_map(config['tools'][tool]).items():
        installed = prefix / ENVIRONMENT_BIN / executable
        command_ok = _is_executable(installed)
        result.append((command_ok, f'Executable in environment: {installed}'))
        dest = bin_dir / command
        launcher_ok = _current_launcher(dest, tool, command, executable, config)
        result.append((launcher_ok, f'Current executable launcher: {dest}'))
    for database in config['tools'][tool].get('databases', []):
        result.append(_database_check(config, tool, database))
    return result


def _database_check(config, tool, database):
    db_path = _resolved_database_path(config, database)
    kind = database.get('type', 'directory')
    exists = db_path.is_file() if kind == 'file' else db_path.is_dir()
    required = database.get('required', False)
    readable = exists and os.access(db_path, os.R_OK | (0 if kind == 'file' else os.X_OK))
    path_exists = db_path.exists() or db_path.is_symlink()
    ok = readable or (not required and not path_exists)
    state = 'required database' if required else 'optional database'
    detail = 'available' if readable else (
        'not installed' if not path_exists else ('wrong type' if not exists else 'not accessible')
    )
    return ok, f'{state} {database["name"]} {detail}: {db_path}'


def check_database(config, tool, database_name):
    database = _database_spec(config, tool, database_name)
    return _database_check(config, tool, database)


def _reject_source_symlinks(source):
    if source.is_symlink():
        raise BioenvError(f'Symbolic links are not allowed in import sources: {source}')
    if source.is_dir():
        for path in source.rglob('*'):
            if path.is_symlink():
                raise BioenvError(f'Symbolic links are not allowed in import sources: {path}')


def database_import_plan(config, tool, database_name, file=None, source=None):
    database = _database_spec(config, tool, database_name)
    if (file is None) == (source is None):
        raise BioenvError('Provide exactly one of --file or --source')
    kind = database.get('type', 'directory')
    raw_origin = Path(file if file is not None else source).expanduser()
    expected_origin = 'file' if file is not None else 'directory'
    if raw_origin.is_symlink():
        raise BioenvError(f'Symbolic links are not allowed as import sources: {raw_origin}')
    origin = raw_origin.resolve(strict=False)
    if not origin.exists():
        raise BioenvError(f'Import source does not exist: {origin}')
    if (origin.is_file() if expected_origin == 'file' else origin.is_dir()) is False:
        raise BioenvError(f'Import source must be an accessible {expected_origin}: {origin}')
    access = os.R_OK | (os.X_OK if expected_origin == 'directory' else 0)
    if not os.access(origin, access):
        raise BioenvError(f'Import source is not accessible: {origin}')
    _reject_source_symlinks(origin)
    if (kind == 'file') != (file is not None):
        option = '--file' if kind == 'file' else '--source'
        raise BioenvError(f'Database {database_name!r} is configured as a {kind}; use {option}')
    root, destination = _managed_database_path(config, database)
    try:
        if source is not None and destination.parent.resolve(strict=False).is_relative_to(origin):
            raise BioenvError('Cannot import a directory into itself or one of its descendants')
        if source is not None and origin.is_relative_to(destination):
            raise BioenvError('Cannot import a directory into itself or one of its descendants')
        if destination.exists() or destination.is_symlink():
            raise BioenvError(f'Database destination already exists: {destination}')
    except BioenvError:
        raise
    except (OSError, RuntimeError) as exc:
        raise BioenvError(f'Cannot safely validate database import paths: {exc}') from exc
    return {'tool': tool, 'database': database_name, 'source': origin,
            'path': destination, 'type': kind, 'root': root}


def import_database(config, tool, database_name, file=None, source=None, dry_run=False):
    plan = database_import_plan(config, tool, database_name, file=file, source=source)
    if dry_run:
        return plan
    destination = plan['path']
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Revalidate after parent creation to catch symlinked paths introduced meanwhile.
        plan = database_import_plan(config, tool, database_name, file=file, source=source)
        destination = plan['path']
        if plan['type'] == 'directory':
            with tempfile.TemporaryDirectory(prefix='.bioenv-import-', dir=destination.parent) as temp:
                staged = Path(temp) / 'payload'
                shutil.copytree(plan['source'], staged, symlinks=False)
                os.replace(staged, destination)
        else:
            fd, temp_name = tempfile.mkstemp(prefix='.bioenv-import-', dir=destination.parent)
            os.close(fd)
            staged = Path(temp_name)
            try:
                shutil.copy2(plan['source'], staged)
                os.replace(staged, destination)
            finally:
                staged.unlink(missing_ok=True)
    except BioenvError:
        raise
    except OSError as exc:
        raise BioenvError(f'Could not import database to {destination}: {exc}') from exc
    return plan
