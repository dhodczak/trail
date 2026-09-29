from __future__ import annotations

import shlex
from collections.abc import Iterable, Sequence
from glob import iglob
from pathlib import Path
from typing import Final

from prompt_toolkit import print_formatted_text, prompt
from prompt_toolkit.completion import PathCompleter, WordCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent

from trail.cli.command import MAGIC
from trail.cli.theme import STYLE
from trail.markers import PREMADE, Markers
from trail.trail import Trail

WEBSITE: Final = "https://geojupyter.github.io/trail"
PREMADE_NAME: Final = "default"
# offered by the toggle console alongside whatever is already chosen
COMMON: Final[tuple[str, ...]] = (
    ".csv",
    ".tsv",
    ".txt",
    ".json",
    ".geojson",
    ".gpkg",
    ".shp",
    ".parquet",
    ".feather",
    ".tif",
    ".tiff",
    ".nc",
    ".h5",
    ".xlsx",
    ".pkl",
    ".npy",
    ".png",
    ".jpg",
)
YES: Final = ("y", "yes")
NO: Final = ("n", "no")


def say(
        text: str,
        style: str = "",
) -> None:
    print_formatted_text(FormattedText([(style, text)]), style=STYLE)


def hint(*lines: str) -> None:
    for line in lines:
        say(f"  - {line}", "class:info")


def ask(
        question: str,
        default: bool,
) -> bool:
    choices = "[Y/n]" if default else "[y/N]"
    while True:
        answer = (
            prompt(f"{choices} ")
            .strip()
            .lower()
        )
        if not answer:
            return default
        if answer in YES:
            return True
        if answer in NO:
            return False
        say(f"{question}: answer y or n", "class:error")


def marker(line: str) -> str | None:
    """The marker a line of a markers file spells; None for a comment, a blank or a bad line."""
    stripped = line.strip()
    if (
        not stripped
        or stripped.startswith("#")
    ):
        return None
    try:
        return Markers.normalize(stripped)
    except ValueError:
        return None


def parse(lines: Iterable[str]) -> set[str]:
    out = {
        marker(line)
        for line in lines
    }
    out.discard(None)
    return out


def compose(
        lines: Sequence[str],
        markers: set[str],
) -> str:
    """
    The text of a markers file holding exactly `markers`, built on `lines` so that their comments
    and ordering survive. A line that is not a valid extension is dropped along with the markers
    that were toggled off.
    """
    kept = [
        line
        for line in lines
        if (
            marker(line) in markers
            or not line.strip()
            or line.strip().startswith("#")
        )
    ]
    present = parse(kept)
    kept.extend(sorted(markers - present))
    text = "".join(
        f"{line}\n"
        for line in kept
    )
    return text


def toggle(markers: set[str]) -> set[str]:
    out = set(markers)
    while True:
        if out:
            say(f"  markers: {' '.join(sorted(out))}", "class:kind")
        else:
            say("  markers: none", "class:kind")
        offered = sorted(set(COMMON) | out)
        completer = WordCompleter(offered, sentence=True)
        answer = prompt(
            "  toggle an extension (blank to finish): ",
            completer=completer,
        ).strip()
        if not answer:
            return out
        for token in answer.split():
            try:
                extension = Markers.normalize(token)
            except ValueError as error:
                say(f"  {error}", "class:error")
                continue
            if extension in out:
                out.remove(extension)
                say(f"  - {extension}", "class:event.offtrailed")
            else:
                out.add(extension)
                say(f"  + {extension}", "class:event.tracked")


def resolve(
        line: str,
        root: Path,
) -> list[Path]:
    """
    The paths one pasted line names. A line naming an existing path is that path, spaces and
    all; anything else is split the way a shell would, so `ls` output pastes as well as a list.
    """
    whole = root / Path(line).expanduser()
    if whole.exists():
        return [whole.resolve()]
    out: list[Path] = []
    for token in shlex.split(line):
        expanded = Path(token).expanduser()
        if any(
            char in token
            for char in MAGIC
        ):
            matches = sorted(
                (root / match).resolve()
                for match in iglob(str(expanded), root_dir=root, recursive=True)
            )
            if not matches:
                say(f"  no matches: {token}", "class:error")
            out.extend(matches)
            continue
        path = (root / expanded).resolve()
        if path.exists():
            out.append(path)
        else:
            say(f"  no such path: {token}", "class:error")
    return out


def gather(root: Path) -> list[Path]:
    bindings = KeyBindings()

    # a paste arrives whole, newlines included; typed, a blank line is what ends the list
    @bindings.add("enter")
    def _(event: KeyPressEvent) -> None:
        buffer = event.current_buffer
        if buffer.document.current_line.strip():
            buffer.newline(copy_margin=False)
        else:
            buffer.validate_and_handle()

    completer = PathCompleter(
        expanduser=True,
        get_paths=lambda: [str(root)],
    )
    text = prompt(
        "  paths (blank line to finish):\n",
        multiline=True,
        key_bindings=bindings,
        completer=completer,
        prompt_continuation="  ",
    )
    selected: dict[Path, None] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            paths = resolve(line, root)
        except ValueError as error:
            say(f"  {line}: {error}", "class:error")
            continue
        for path in paths:
            selected[path] = None
    return list(selected)


def startup(trail: Trail) -> None:
    """Placeholder: run `trail` in the background on login. Not yet asked or implemented."""


def init(root: Path) -> Trail:
    """
    Walk through starting a Trail in `root`, or re-initializing the one that encloses it. Every
    question is asked before anything is written, so an interrupt leaves the project untouched.
    Re-initializing rewrites the markers and adds to what is tracked; the log is kept.
    """
    existing = Trail.locate(root)
    if existing is None:
        home = root
    else:
        home = existing.parent
    path = home / ".markers"
    premade = (PREMADE / f"{PREMADE_NAME}.markers").read_text(encoding="utf-8").splitlines()
    defaults = sorted(parse(premade))

    say("")
    say("🌲 Welcome to Trail! 🌳", "bold")
    say("")
    say("Trail watches a project directory and records what happens to its files: when they")
    say("are created, modified, moved or deleted, so you can follow how your data came to be.")
    say(f"Read more at {WEBSITE}", "class:info")
    say("")
    if existing is None:
        say("Let's start a Trail! 🥾", "bold")
    else:
        say(f"Re-initializing the Trail at {existing}; its log is kept. 🥾", "bold")
    say("")

    say("How would you like to set some markers? 🪧", "bold")
    hint(
        "Markers indicate what should be followed.",
        "Think of `.gitignore`: but instead of ignoring assets, you are automatically tracking them.",
    )
    say("")

    say("Would you like to accept premade markers? (Y/N) 🪨", "bold")
    hint(f"By default, premade includes tracking common assets such as {', '.join(defaults)}.")
    if ask("premade markers", default=True):
        markers = set(defaults)
    else:
        markers = set()
    say("")

    say("Would you like to add/remove specific extensions as markers? 🏷️ (Y/N)", "bold")
    hint(
        "The interactive console will let you toggle extensions.",
        "You can always edit the `.markers` text file afterward.",
    )
    if ask("toggle extensions", default=False):
        markers = toggle(markers)
    say("")

    say("Would you like to track any specific assets or directories? (Y/N)", "bold")
    hint("You can always use `trail track` and `trail offtrail` to add or remove paths later.")
    if ask("track paths", default=False):
        paths = gather(root)
    else:
        paths = []
    say("")

    # `trail` on startup is asked here once `startup` is implemented

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    elif markers & set(defaults):
        lines = premade
    else:
        lines = []
    path.write_text(compose(lines, markers), encoding="utf-8")
    # the markers are on file before the Trail opens, so the files they mark are tracked with
    # the project directory rather than after it
    trail = Trail(root, markers=None)
    for tracked in paths:
        if tracked in trail.entries:
            continue
        try:
            trail.track(tracked)
        except (OSError, ValueError) as error:
            say(f"{tracked}: {error}", "class:error")

    say(f"Trail started in {home} 🌲", "bold")
    say(f"  markers: {' '.join(trail.markers) or 'none'}", "class:info")
    say(f"  tracked: {len(trail.assets)} assets, {len(trail.dirs)} dirs", "class:info")
    say("")
    return trail
