"""Command line interface for bioenv."""

import argparse
import os
import shlex
import sys
from pathlib import Path

from bioenv import __version__
from bioenv.config import DEFAULT_CONFIG, ConfigError, load_config
from bioenv.manager import (
    ENVIRONMENT_BIN,
    MICROMAMBA_ROOT_ENV,
    BioenvError,
    check,
    check_database,
    command_map,
    install,
    install_database,
    paths,
    plan_update,
    publish,
    remove,
    remove_plan,
    update,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='bioenv', description='Manage shared Micromamba bioinformatics environments.'
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument(
        '--config', type=Path, default=Path(os.environ.get('BIOENV_CONFIG', DEFAULT_CONFIG)),
        help='YAML catalog path',
    )
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='List tools declared in catalog')
    for name, help_text in (
        ('info', 'Show tool configuration and paths'),
        ('inspect', 'List executable files in tool environment/bin'),
        ('check', 'Check environment, launchers and databases'),
        ('install', 'Create environment and publish selected commands'),
        ('update', 'Update an installed tool in place'),
        ('publish', 'Publish commands from an existing environment'),
        ('remove', 'Remove a tool environment and its managed launchers'),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument('tool', help='Tool identifier from catalog')
        if name in ('install', 'publish', 'update', 'remove'):
            sub.add_argument('--dry-run', action='store_true', help='Show actions without changes')
        if name == 'update':
            sub.add_argument(
                '--latest', action='store_true', help='Ignore the catalog package version pin'
            )
        if name == 'remove':
            sub.add_argument('--launchers-only', action='store_true',
                             help='Remove launchers while keeping the environment')
            sub.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    db = commands.add_parser('db', help='Check or install a declared database')
    db_commands = db.add_subparsers(dest='db_command', required=True)
    db_install = db_commands.add_parser('install', help='Install a declared database')
    db_install.add_argument('tool', help='Tool identifier from catalog')
    db_install.add_argument('database', help='Database name from catalog')
    db_install.add_argument('--dry-run', action='store_true', help='Show actions without changes')
    db_check = db_commands.add_parser('check', help='Check a declared database')
    db_check.add_argument('tool', help='Tool identifier from catalog')
    db_check.add_argument('database', help='Database name from catalog')
    commands.add_parser('validate', help='Validate YAML catalog')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        tools = config['tools']
        if args.command == 'validate':
            print(f'OK: {len(tools)} tool(s) declared in {args.config}')
            return 0
        if args.command == 'list':
            for name, spec in sorted(tools.items()):
                print(f"{name}\t{spec['package']}\t{', '.join(command_map(spec))}")
            return 0
        if args.command == 'db':
            if args.tool not in tools:
                raise ConfigError(f'Unknown tool: {args.tool}')
            if args.db_command == 'check':
                ok, label = check_database(config, args.tool, args.database)
                print(f'{"OK" if ok else "FAIL"}  {label}')
                return 0 if ok else 1
            plan = install_database(
                config, args.tool, args.database, dry_run=args.dry_run,
            )
            print(f'Tool: {plan["tool"]}')
            print(f'Database: {plan["database"]}')
            print(f'Path: {plan["path"]}')
            if args.dry_run:
                print('Directories to create:')
                if plan['directories']:
                    for directory in plan['directories']:
                        print(f'  {directory}')
                else:
                    print('  none')
                print('Micromamba: ' + shlex.join(plan['command']))
                print('Dry run: no files or directories were created.')
            else:
                print(f'Installed database {args.database} for {args.tool} at {plan["path"]}')
            return 0
        if args.tool not in tools:
            raise ConfigError(f'Unknown tool: {args.tool}')
        tool = args.tool
        spec = tools[tool]
        prefix, bin_dir = paths(config, tool)
        if args.command == 'info':
            print(f"Tool: {tool}\nPackage: {spec['package']}\nEnvironment: {prefix}")
            if 'version' in spec:
                print(f"Version requested: {spec['version']}")
            if 'python' in spec:
                print(f"Python requested: {spec['python']}")
            commands = command_map(spec)
            print(f"Commands: {', '.join(commands)}\nLaunchers: {bin_dir}")
            aliases = [f'{public} -> {executable}' for public, executable in commands.items()
                       if public != executable]
            if aliases:
                print(f"Executable aliases: {', '.join(aliases)}")
            for key, value in spec.get('environment', {}).items():
                print(f'Environment: {key}={value}')
        elif args.command == 'inspect':
            bindir = prefix / ENVIRONMENT_BIN
            if not bindir.is_dir():
                raise BioenvError(f'No environment bin directory: {bindir}')
            for p in sorted(bindir.iterdir()):
                if p.is_file() and os.access(p, os.X_OK):
                    declared = [public for public, executable in command_map(spec).items()
                                if executable == p.name]
                    suffix = f" (launcher: {', '.join(declared)})" if declared else ''
                    print(p.name + suffix)
        elif args.command == 'check':
            results = check(config, tool)
            for ok, label in results:
                print(f"{'OK' if ok else 'FAIL'}  {label}")
            return 0 if all(ok for ok, _ in results) else 1
        elif args.command == 'install':
            command, launchers = install(config, tool, dry_run=args.dry_run)
            if args.dry_run:
                print(f'{MICROMAMBA_ROOT_ENV}={shlex.quote(config["settings"]["mamba_root"])}')
                print('Micromamba: ' + shlex.join(command))
                for launcher in launchers:
                    print('Publish: ' + launcher)
            else:
                print(f'Installed {tool} in {prefix}')
        elif args.command == 'update':
            command, launchers = plan_update(config, tool, latest=args.latest)
            mode = 'latest resolvable' if args.latest else 'catalog specification'
            spec_version = (
                'ignored (--latest)' if args.latest else spec.get('version', 'not pinned')
            )
            print(f'Tool: {tool}')
            print(f'Environment: {prefix}')
            print(f'Package: {spec["package"]}')
            print(f'Catalog version: {spec_version}')
            print(f'Mode: {mode}')
            print(f'Operation: {shlex.join(command)}')
            if args.dry_run:
                print('Micromamba solver simulation (--dry-run):')
            _, launchers, old_version, installed_version = update(
                config, tool, latest=args.latest, dry_run=args.dry_run,
            )
            if args.dry_run:
                for launcher in launchers:
                    print(f'Would synchronize launcher: {launcher}')
                print('Solver simulation completed; no environment or launcher changes were made.')
            else:
                if installed_version == old_version:
                    print(
                        f'Updated {tool} in {prefix}; package version remains {installed_version}. '
                        'Micromamba completed without changing the main package version.'
                    )
                else:
                    print(
                        f'Updated {tool} in {prefix}; package version '
                        f'{old_version} -> {installed_version}'
                    )
        elif args.command == 'publish':
            launchers = publish(config, tool, dry_run=args.dry_run)
            for launcher in launchers:
                print(f"{'Would publish' if args.dry_run else 'Published'}: {launcher}")
        elif args.command == 'remove':
            plan = remove_plan(config, tool, launchers_only=args.launchers_only)
            print(f'Tool: {tool}')
            print(f'Environment: {prefix}' + (
                ' (preserved by --launchers-only)' if args.launchers_only else ''
            ))
            if not args.launchers_only:
                if plan['command']:
                    print('Micromamba: ' + shlex.join(plan['command']))
                elif not plan['environment_exists']:
                    print('Environment: already absent; no environment removal will be attempted')
            print('Launchers:')
            for launcher, state in plan['launchers']:
                print(f'  {launcher} ({state})')
            if plan['blocked']:
                print('Removal blocked by launcher conflicts.', file=sys.stderr)
                return 2
            if args.dry_run:
                print('Dry run: no environment or launcher changes were made.')
                return 0
            if not args.launchers_only and not plan['environment_exists']:
                print(f'Environment for {tool} is already absent; Micromamba will not run.')
                if any(state == 'managed' for _, state in plan['launchers']):
                    print('Use --launchers-only to remove the remaining managed launchers.')
                else:
                    print('No managed launchers remain.')
                return 0
            if not args.yes:
                if not sys.stdin.isatty():
                    raise BioenvError(
                        'Removal requires an interactive terminal; rerun with --yes to confirm.'
                    )
                action = 'Remove these launchers and keep the environment?' if args.launchers_only \
                    else 'Remove this environment and its managed launchers?'
                try:
                    answer = input(f'{action} [y/N] ')
                except EOFError as exc:
                    raise BioenvError('No confirmation received; removal cancelled.') from exc
                except KeyboardInterrupt:
                    print('\nRemoval cancelled.')
                    return 1
                if answer.strip().casefold() not in ('y', 'yes'):
                    print('Removal cancelled.')
                    return 1
            result = remove(config, tool, launchers_only=args.launchers_only)
            if args.launchers_only:
                print(f'Removed managed launchers for {tool}; environment was preserved.')
            elif result['environment_exists']:
                print(f'Removed environment and managed launchers for {tool}.')
            else:
                print(f'Environment for {tool} was already absent; no resources were removed.')
        return 0
    except (ConfigError, BioenvError) as exc:
        print(f'bioenv: {exc}', file=sys.stderr)
        return 2
    except OSError as exc:
        print(f'bioenv: filesystem operation failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
