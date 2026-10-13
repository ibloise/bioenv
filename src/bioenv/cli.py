"""Small administrative CLI; non-mutating foundation only."""

import argparse
from pathlib import Path

from bioenv import __version__
from bioenv.config import DEFAULT_CONFIG, ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bioenv",
        description="Manage shared Micromamba bioinformatics tools (initial scaffold).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="YAML catalog path"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List tools declared in the catalog")
    info = commands.add_parser("info", help="Show a tool's declared configuration")
    info.add_argument("tool", help="Tool identifier in the YAML catalog")
    commands.add_parser("validate", help="Validate the YAML catalog")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"bioenv: {exc}", file=__import__("sys").stderr)
        return 2

    tools = config["tools"]
    if args.command == "validate":
        print(f"OK: {len(tools)} tool(s) declared in {args.config}")
    elif args.command == "list":
        for name, spec in sorted(tools.items()):
            print(f"{name}\t{spec['package']}\t{', '.join(spec['commands'])}")
    elif args.command == "info":
        if args.tool not in tools:
            print(f"bioenv: unknown tool: {args.tool}", file=__import__("sys").stderr)
            return 2
        spec = tools[args.tool]
        print(f"Tool: {args.tool}\nPackage: {spec['package']}")
        if "version" in spec:
            print(f"Version: {spec['version']}")
        if "python" in spec:
            print(f"Python: {spec['python']}")
        print(f"Commands: {', '.join(spec['commands'])}")
        for key, value in spec.get("environment", {}).items():
            print(f"Environment: {key}={value}")
    return 0
