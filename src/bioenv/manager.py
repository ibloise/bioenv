"""Manage stable Micromamba prefixes and explicitly declared command launchers."""

import os
from pathlib import Path
import shlex
import subprocess

MARKER = '# Managed by bioenv; tool='
SHELL_SHEBANG = '#!/bin/sh'
ENVIRONMENT_BIN = 'bin'
MICROMAMBA_ROOT_ENV = 'MAMBA_ROOT_PREFIX'
CREATE_COMMAND = 'create'
PREFIX_OPTION = '-p'
YES_OPTION = '-y'
PYTHON_PACKAGE = 'python'


class BioenvError(RuntimeError):
    """Unsafe operation or external command failure."""


def paths(config, tool):
    settings = config['settings']
    return (Path(settings['envs_root']) / tool, Path(settings['bin_dir']))


def _wrapper(tool, command, config):
    spec = config['tools'][tool]
    prefix, _ = paths(config, tool)
    micromamba = config['settings']['micromamba']
    lines = [SHELL_SHEBANG, f'{MARKER}{tool}']
    for key, value in spec.get('environment', {}).items():
        lines.append(f'export {key}={shlex.quote(value)}')
    lines.append(f'exec {shlex.quote(micromamba)} run -p {shlex.quote(str(prefix))} '
                 f'{shlex.quote(command)} "$@"')
    return '\n'.join(lines) + '\n'


def _owned(path, tool):
    if path.is_symlink() or not path.is_file():
        return False


def _current_launcher(path, tool, command, config):
    if not _owned(path, tool) or not os.access(path, os.X_OK):
        return False
    try:
        return path.read_text(encoding='utf-8') == _wrapper(tool, command, config)
    except (OSError, UnicodeError):
        return False
    try:
        return path.read_text(encoding='utf-8').splitlines()[1] == MARKER + tool
    except (OSError, UnicodeError, IndexError):
        return False


def _preflight(config, tool):
    prefix, bin_dir = paths(config, tool)
    if prefix.is_symlink():
        raise BioenvError(f'Refusing symlink environment: {prefix}')
    for command in config['tools'][tool]['commands']:
        dest = bin_dir / command
        if (dest.exists() or dest.is_symlink()) and not _owned(dest, tool):
            raise BioenvError(f'Command exists and is not owned by bioenv: {dest}')
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
    return command, [str(bin_dir / c) for c in spec['commands']]


def install(config, tool, dry_run=False):
    prefix, _ = _preflight(config, tool)
    if prefix.exists():
        raise BioenvError(f'Environment already exists: {prefix}; use publish for existing installations')
    command, launchers = plan(config, tool)
    if dry_run:
        return command, launchers
    env = os.environ.copy()
    env[MICROMAMBA_ROOT_ENV] = config['settings']['mamba_root']
    try:
        subprocess.run(command, env=env, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BioenvError(f'Could not create environment for {tool} with Micromamba: {exc}') from exc
    try:
        publish(config, tool)
    except BioenvError as exc:
        raise BioenvError(
            f'Environment for {tool} was created at {prefix}, but launcher publication failed: '
            f'{exc}. Fix the issue and run "bioenv publish {tool}" to finish.'
        ) from exc
    return command, launchers


def publish(config, tool, dry_run=False):
    prefix, bin_dir = _preflight(config, tool)
    if not prefix.is_dir():
        raise BioenvError(f'Environment for {tool} is missing or is not a directory: {prefix}')
    commands = config['tools'][tool]['commands']
    missing = [c for c in commands if not (prefix / ENVIRONMENT_BIN / c).is_file()
               or not os.access(prefix / ENVIRONMENT_BIN / c, os.X_OK)]
    if missing:
        raise BioenvError(
            f'Environment for {tool} is missing executable(s): {", ".join(missing)}. '
            'Check the package contents or update the catalog commands.'
        )
    if dry_run:
        return [bin_dir / c for c in commands]
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BioenvError(f'Cannot create launcher directory {bin_dir}: {exc}') from exc
    # Never overwrite files that bioenv doesn't own, even if preflight ran earlier.
    for command in commands:
        dest = bin_dir / command
        content = _wrapper(tool, command, config)
        if dest.exists() or dest.is_symlink():
            if not _owned(dest, tool):
                raise BioenvError(f'Refusing to overwrite: {dest}')
            try:
                dest.write_text(content, encoding='utf-8')
            except OSError as exc:
                raise BioenvError(f'Cannot update managed launcher {dest}: {exc}') from exc
        else:
            try:
                with dest.open('x', encoding='utf-8') as file:
                    file.write(content)
            except FileExistsError as exc:
                raise BioenvError(f'Refusing to overwrite launcher created by another process: {dest}') from exc
            except OSError as exc:
                raise BioenvError(f'Cannot create launcher {dest}: {exc}') from exc
        try:
            dest.chmod(0o755)
        except OSError as exc:
            raise BioenvError(f'Cannot make launcher executable {dest}: {exc}') from exc
    return [bin_dir / c for c in commands]


def check(config, tool):
    prefix, bin_dir = paths(config, tool)
    result = []
    environment_ok = prefix.is_dir() and not prefix.is_symlink()
    result.append((environment_ok, f'Environment directory: {prefix}'))
    for command in config['tools'][tool]['commands']:
        installed = prefix / ENVIRONMENT_BIN / command
        command_ok = installed.is_file() and os.access(installed, os.X_OK)
        result.append((command_ok, f'Executable in environment: {installed}'))
        dest = bin_dir / command
        launcher_ok = _current_launcher(dest, tool, command, config)
        result.append((launcher_ok, f'Current executable launcher: {dest}'))
    for db in config['tools'][tool].get('databases', []):
        db_path = Path(db['path'])
        exists = db_path.is_dir()
        required = db.get('required', False)
        readable = exists and os.access(db_path, os.R_OK | os.X_OK)
        ok = readable or (not required and not exists)
        state = 'required database' if required else 'optional database'
        detail = 'available' if readable else ('not installed' if not exists else 'not readable')
        result.append((ok, f'{state} {db["name"]} {detail}: {db_path}'))
    return result
