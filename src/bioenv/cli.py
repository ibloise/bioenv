"""Command line interface for bioenv."""

import argparse
import os
from pathlib import Path
import shlex
import sys

from bioenv import __version__
from bioenv.config import DEFAULT_CONFIG, ConfigError, load_config
from bioenv.manager import (
    ENVIRONMENT_BIN,
    BioenvError,
    check,
    install,
    paths,
    publish,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='bioenv', description='Manage shared Micromamba bioinformatics environments.'
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG, help='YAML catalog path')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='List tools declared in catalog')
    for name, help_text in (
        ('info', 'Show tool configuration and paths'),
        ('inspect', 'List executable files in tool environment/bin'),
        ('check', 'Check environment, launchers and databases'),
        ('install', 'Create environment and publish selected commands'),
        ('publish', 'Publish commands from an existing environment'),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument('tool', help='Tool identifier from catalog')
        if name in ('install', 'publish'):
            sub.add_argument('--dry-run', action='store_true', help='Show actions without changes')
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
                print(f"{name}\t{spec['package']}\t{', '.join(spec['commands'])}")
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
            print(f"Commands: {', '.join(spec['commands'])}\nLaunchers: {bin_dir}")
            for key, value in spec.get('environment', {}).items():
                print(f'Environment: {key}={value}')
        elif args.command == 'inspect':
            bindir = prefix / ENVIRONMENT_BIN
            if not bindir.is_dir():
                raise BioenvError(f'No environment bin directory: {bindir}')
            for p in sorted(bindir.iterdir()):
                if p.is_file() and os.access(p, os.X_OK):
                    print(p.name)
        elif args.command == 'check':
            results = check(config, tool)
            for ok, label in results:
                print(f"{'OK' if ok else 'FAIL'}  {label}")
            return 0 if all(ok for ok, _ in results) else 1
        elif args.command == 'install':
            command, launchers = install(config, tool, dry_run=args.dry_run)
            if args.dry_run:
                print('Micromamba: ' + shlex.join(command))
                for launcher in launchers:
                    print('Publish: ' + launcher)
            else:
                print(f'Installed {tool} in {prefix}')
        elif args.command == 'publish':
            launchers = publish(config, tool, dry_run=args.dry_run)
            for launcher in launchers:
                print(f"{'Would publish' if args.dry_run else 'Published'}: {launcher}")
        return 0
    except (ConfigError, BioenvError) as exc:
        print(f'bioenv: {exc}', file=sys.stderr)
        return 2
    except OSError as exc:
        print(f'bioenv: filesystem operation failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
