"""Command line interface for bioenv."""

import argparse
import os
import shlex
import subprocess
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
    database_status,
    import_database,
    install,
    installation_status,
    paths,
    plan_update,
    publish,
    read_installed_version,
    remove,
    remove_plan,
    update,
)


def _format_table(headers, rows):
    widths = [max([len(header), *(len(row[index]) for row in rows)])
              for index, header in enumerate(headers)]
    lines = [
        '  '.join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        '  '.join('─' * width for width in widths),
    ]
    lines.extend(
        '  '.join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip()
        for row in rows
    )
    return '\n'.join(lines)


def _config_path(path):
    return path.expanduser().resolve()


def _edit_config(path, editor):
    path = _config_path(path)
    editable_path = path if path.exists() else path.parent
    if not os.access(editable_path, os.W_OK):
        raise BioenvError(
            f'Catalog is not writable by this user: {path}. '
            f'Use `sudoedit {shlex.quote(str(path))}` to edit the protected catalog.'
        )

    try:
        command = shlex.split(editor)
    except ValueError as exc:
        raise BioenvError(f'Invalid editor command: {exc}') from exc
    if not command:
        raise BioenvError('Editor command is empty')
    try:
        result = subprocess.run([*command, str(path)], check=False)
    except OSError as exc:
        raise BioenvError(f'Cannot run editor {command[0]!r}: {exc}') from exc
    if result.returncode:
        raise BioenvError(f'Editor {command[0]!r} exited with status {result.returncode}')
    return path


def _positive_integer(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('must be a positive integer') from exc
    if number <= 0:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def _tool_inventory(config, tools, names):
    rows = {}
    for name in names:
        status, _ = installation_status(config, name)
        version = read_installed_version(config, name) or '—'
        rows[name] = (name, version, status, ', '.join(command_map(tools[name])))
    return rows


def _print_tool_group(title, rows):
    print(title)
    print(_format_table(('TOOL', 'VERSION', 'STATUS', 'COMMANDS'), rows))


def _install_pending(config, tools, category, limit, dry_run):
    names = [
        name for name, spec in sorted(tools.items())
        if category is None or category in spec.get('categories', [])
    ]
    states = {name: installation_status(config, name)[0] for name in names}
    pending = [name for name in names if states[name] == 'Missing']
    present = [name for name in names if states[name] != 'Missing']
    selected = pending if limit is None else pending[:limit]
    deferred = pending[len(selected):]

    if dry_run:
        selected_set = set(selected)
        deferred_set = set(deferred)
        rows = [
            (name, tools[name]['package'], states[name],
             'Install' if name in selected_set else
             'Defer' if name in deferred_set else 'Skip')
            for name in names
        ]
        print('INSTALLATION PLAN\n')
        print(_format_table(('TOOL', 'PACKAGE', 'STATUS', 'ACTION'), rows))
        print(f'\nPending installations: {len(selected)}')
        print(f'Already present:      {len(present)}')
        print(f'Deferred by limit:     {len(deferred)}')
        incomplete = [name for name in present if states[name] == 'Incomplete']
        if incomplete:
            print('Incomplete environments need administrative review: ' + ', '.join(incomplete))
        return 0
    if not selected:
        print('No pending tools.')
        incomplete = [name for name in present if states[name] == 'Incomplete']
        if incomplete:
            print('Incomplete environments need administrative review: ' + ', '.join(incomplete))
        return 0

    print(f'Installing {len(selected)} pending tool(s)...')
    installed = []
    failed = []
    for index, name in enumerate(selected, start=1):
        print(f'[{index}/{len(selected)}] {name} ', end='', flush=True)
        try:
            install(config, name)
        except BioenvError as exc:
            failed.append((name, str(exc)))
            print(f'FAILED: {exc}')
        except OSError as exc:
            failed.append((name, str(exc)))
            print(f'FAILED: {exc}')
        else:
            installed.append(name)
            print('OK')

    print('\nSUMMARY')
    print(f'  Installed: {len(installed)}')
    print(f'  Failed:    {len(failed)}')
    print(f'  Skipped:   {len(present)}')
    print(f'  Deferred:  {len(deferred)}')
    if failed:
        print('Failed tools: ' + ', '.join(name for name, _ in failed))
        for name, reason in failed:
            print(f'  {name}: {reason}', file=sys.stderr)
    if deferred:
        print('Pending tools not processed by --limit: ' + ', '.join(deferred))
    if any(states[name] == 'Incomplete' for name in present):
        incomplete = [name for name in present if states[name] == 'Incomplete']
        print('Incomplete environments need administrative review: ' + ', '.join(incomplete))
    return 1 if failed else 0


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
    list_parser = commands.add_parser('list', help='List tools declared in catalog')
    list_parser.add_argument('--simple', action='store_true', help='Print only tool identifiers')
    list_parser.add_argument('--category', help='Show tools in this category')
    list_parser.add_argument('--group-by', choices=('category',), help='Group tools by category')
    for name, help_text in (
        ('info', 'Show tool configuration and paths'),
        ('inspect', 'List executable files in tool environment/bin'),
        ('check', 'Check environment, launchers and databases'),
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
    install_parser = commands.add_parser('install', help='Install one tool or pending tools')
    install_parser.add_argument('tool', nargs='?', help='Tool identifier from catalog')
    install_parser.add_argument('--dry-run', action='store_true', help='Show actions without changes')
    install_parser.add_argument('--pending', action='store_true', help='Install missing environments')
    install_parser.add_argument('--category', help='Limit pending tools to this category')
    install_parser.add_argument('--limit', type=_positive_integer,
                                help='Maximum pending tools to install in this batch')
    db = commands.add_parser('db', help='Import or check a declared database')
    db_commands = db.add_subparsers(dest='db_command', required=True)
    db_import = db_commands.add_parser('import', help='Copy a prepared database into storage')
    db_import.add_argument('tool', help='Tool identifier from catalog')
    db_import.add_argument('database', help='Database name from catalog')
    db_import.add_argument('--dry-run', action='store_true', help='Validate and show paths only')
    origins = db_import.add_mutually_exclusive_group(required=True)
    origins.add_argument('--file', type=Path, help='Prepared database file (catalog type: file)')
    origins.add_argument('--source', type=Path, help='Prepared database directory')
    db_check = db_commands.add_parser('check', help='Check a declared database')
    db_check.add_argument('tool', help='Tool identifier from catalog')
    db_check.add_argument('database', help='Database name from catalog')
    commands.add_parser('validate', help='Validate YAML catalog')
    commands.add_parser('categories', help='List declared tool categories')
    config_parser = commands.add_parser('config', help='Locate or edit the YAML catalog')
    config_commands = config_parser.add_subparsers(dest='config_command', required=True)
    config_commands.add_parser('path', help='Print the active catalog path')
    config_edit = config_commands.add_parser('edit', help='Edit and validate the active catalog')
    config_edit.add_argument('--editor', help='Editor command (may include arguments)')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == 'install':
            if not args.pending and (args.category is not None or args.limit is not None):
                raise BioenvError('--category and --limit require --pending')
            if args.pending and args.tool:
                raise BioenvError('Do not combine TOOL with --pending')
            if not args.pending and not args.tool:
                raise BioenvError('install requires TOOL or --pending')
        if args.command == 'config':
            path = _config_path(args.config)
            if args.config_command == 'path':
                print(path)
                return 0
            editor = (
                args.editor if args.editor is not None else
                os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'nano'
            )
            path = _edit_config(path, editor)
            try:
                config = load_config(path)
            except ConfigError as exc:
                print(f'Catalog validation failed:\n{exc}', file=sys.stderr)
                return 1
            print(f'Catalog validation: OK ({len(config["tools"])} tools declared)')
            return 0
        config = load_config(args.config)
        tools = config['tools']
        if args.command == 'validate':
            print(f'OK: {len(tools)} tool(s) declared in {args.config}')
            return 0
        if args.command == 'categories':
            counts = {}
            for spec in tools.values():
                for category in spec.get('categories', []):
                    counts[category] = counts.get(category, 0) + 1
            rows = [(category, str(count)) for category, count in sorted(counts.items())]
            if rows:
                print(_format_table(('CATEGORY', 'TOOLS'), rows))
            else:
                print('No categories declared.')
            return 0
        if args.command == 'list':
            names = [
                name for name, spec in sorted(tools.items())
                if args.category is None or args.category in spec.get('categories', [])
            ]
            if args.simple:
                for name in names:
                    print(name)
                return 0
            if not names:
                print(f'No tools found in category: {args.category}' if args.category else
                      'No tools declared.')
                return 0
            tool_rows = _tool_inventory(config, tools, names)
            if args.group_by == 'category':
                if args.category is not None:
                    groups = [(args.category, names)]
                else:
                    category_names = sorted({
                        category for name in names
                        for category in tools[name].get('categories', [])
                    })
                    groups = [
                        (category, [name for name in names
                                    if category in tools[name].get('categories', [])])
                        for category in category_names
                    ]
                    uncategorized = [
                        name for name in names if not tools[name].get('categories', [])
                    ]
                    if uncategorized:
                        groups.append(('UNCATEGORIZED', uncategorized))
                for index, (category, group_names) in enumerate(groups):
                    if index:
                        print()
                    _print_tool_group(category.upper(), [tool_rows[name] for name in group_names])
                return 0
            print(_format_table(('TOOL', 'VERSION', 'STATUS', 'COMMANDS'),
                                [tool_rows[name] for name in names]))
            return 0
        if args.command == 'db':
            if args.tool not in tools:
                raise ConfigError(f'Unknown tool: {args.tool}')
            if args.db_command == 'check':
                ok, label = check_database(config, args.tool, args.database)
                print(f'{"OK" if ok else "FAIL"}  {label}')
                return 0 if ok else 1
            plan = import_database(
                config, args.tool, args.database, dry_run=args.dry_run,
                file=args.file, source=args.source,
            )
            print(f'Tool: {plan["tool"]}')
            print(f'Database: {plan["database"]}')
            print(f'Source: {plan["source"]}')
            print(f'Destination: {plan["path"]}')
            if args.dry_run:
                print('Dry run: no files or directories were created.')
            else:
                print(f'Imported database {args.database} for {args.tool} to {plan["path"]}')
            return 0
        if args.command == 'install' and args.pending:
            return _install_pending(
                config, tools, args.category, args.limit, args.dry_run
            )
        if args.tool not in tools:
            raise ConfigError(f'Unknown tool: {args.tool}')
        tool = args.tool
        spec = tools[tool]
        prefix, bin_dir = paths(config, tool)
        if args.command == 'info':
            status, _ = installation_status(config, tool)
            version = read_installed_version(config, tool)
            print(f'Tool:         {tool}')
            print(f'Package:      {spec["package"]}')
            print(f'Version:      {version or "—"}')
            requested = spec.get('version')
            if requested and requested != version:
                print(f'Requested:    {requested}')
            print(f'Status:       {status}')
            print(f'Environment:  {prefix}')
            categories = spec.get('categories', [])
            if categories:
                print(f'Categories:   {", ".join(categories)}')
            commands = command_map(spec)
            print('\nCommands:')
            for public, executable in commands.items():
                suffix = f' (executable: {executable})' if public != executable else ''
                print(f'  {public}{suffix}')
            print(f'\nLaunchers:    {bin_dir}')
            databases = spec.get('databases', [])
            print('\nDatabases:')
            if databases:
                has_default = any(database.get('env') for database in databases)
                db_rows = []
                for database in databases:
                    db_status, db_path = database_status(config, database)
                    default = 'Yes' if database.get('env') else ('No' if has_default else '—')
                    db_rows.append((database['name'], db_status, default, str(db_path)))
                print(_format_table(('NAME', 'STATUS', 'DEFAULT', 'PATH'), db_rows))
            else:
                print('  None declared')
            for key, value in spec.get('environment', {}).items():
                print(f'Environment variable: {key}={value}')
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
