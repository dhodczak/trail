from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from trail.cli.console import Console
from trail.trail import Trail

__all__ = ["Console", "main", "parse"]


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="trail",
        description="Watch a project directory and record what happens to its resources.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="project directory to open; the working directory by default",
    )
    parser.add_argument(
        "--nodir",
        action="store_true",
        help="keep the log in memory instead of serializing it to PATH/.trail",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse(argv)
    if arguments.path is None:
        root = Path.cwd()
    else:
        root = Path(arguments.path).expanduser().resolve()
    # `trail ./.trail` names the metadata of a project rather than a project of its own
    if root.name == ".trail":
        root = root.parent
    if not root.is_dir():
        print(f"trail: not a directory: {root}", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("trail: the console needs an interactive terminal", file=sys.stderr)
        return 2
    if arguments.nodir:
        trail = Trail()
    else:
        trail = Trail(root)
    console = Console(trail, root)
    try:
        asyncio.run(console.run())
    except KeyboardInterrupt:
        return 130
    if trail.dir is None:
        print(f"trail #{trail.id[:8]}: {len(trail.events)} events, not recorded")
    else:
        print(f"trail #{trail.id[:8]}: {len(trail.events)} events in {trail.dir}")
    return 0
