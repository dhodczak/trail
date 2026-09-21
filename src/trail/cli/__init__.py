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
    if console.restarting:
        relaunch()
    if trail.dir is None:
        print(f"trail #{trail.id[:8]}: {len(trail.events)} events, not recorded")
    else:
        print(f"trail #{trail.id[:8]}: {len(trail.events)} events in {trail.dir}")
    return 0


def relaunch() -> NoReturn:
    """
    Replaces this process with a fresh interpreter on the command line it was given, so that the
    console comes back having reimported everything it is made of. `sys.orig_argv` is the whole
    line rather than the arguments left after the interpreter consumed its own, which is what
    lets `python -m trail` and the `trail` script be restarted the same way; the first token is
    looked up on PATH because that is where the shell found it.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(sys.orig_argv[0], sys.orig_argv)
