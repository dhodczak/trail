from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from trail.cli.console import Console
from trail.trail import Trail

__all__ = ["Console", "main", "parse", "relaunch"]


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
    # `trail ./.trail` names a project's metadata, not a project of its own
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
    if console.restarting:
        relaunch()
    if trail.dir is None:
        print(f"trail #{trail.id}: {len(trail.events)} events, not recorded")
    else:
        print(f"trail #{trail.id}: {len(trail.events)} events in {trail.dir}")
    return 0


def relaunch() -> NoReturn:
    """
    Replace this process with a fresh interpreter running the same command line, so the console
    comes back having reimported everything.

    `sys.orig_argv` is the full command line, interpreter and its flags included, rather than
    what argparse is left with. That is what lets `python -m trail` and the `trail` script
    restart the same way. Its first token is looked up on PATH, since that is how the shell
    found it.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(sys.orig_argv[0], sys.orig_argv)
