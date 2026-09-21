from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from fnmatch import fnmatch
from glob import iglob
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from trail.cli.node import Node
from trail.dir import Dir
from trail.util import Repr

if TYPE_CHECKING:
    from trail.cli.commands import Commands
    from trail.cli.console import Console
    from trail.entry import Entry

# the characters that make a command argument a pattern rather than a path
MAGIC = ("*", "?", "[")
# what confirms an action that discards the record rather than adding to it
FORCE = ("-f", "--force")
# the action that empties a listing, spelled in full because a bare value is a search
CLEAR = "clear"


class Command(Node):
    """
    One verb of the command bar. Subclasses register themselves by name, the way Event subclasses
    do, so that adding a command is adding a class: the help text, the completion offered for its
    arguments and the dispatch table are all read back off the registry.
    """

    _parent: Commands
    classes: ClassVar[dict[str, type[Command]]] = {}
    name: ClassVar[str] = ""
    aliases: ClassVar[tuple[str, ...]] = ()
    usage: ClassVar[str] = ""
    summary: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.name:
            cls.classes[cls.name] = cls

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    def __call__(self, arguments: Sequence[str]) -> None:
        raise NotImplementedError

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Completions for this command's arguments; a command that takes none offers none."""
        return iter(())

    def display(
        self,
        path: str | Path,
        directory: bool = False,
    ) -> str:
        """How this CLI words a path; the panes are handed the same one."""
        return self._renderer.display(path, directory)

    def word(self, document: Document) -> str:
        """The argument being completed, as a document of its own for a nested completer."""
        return document.get_word_before_cursor(WORD=True)

    def tracked(self, word: str) -> Iterator[Completion]:
        """Completions drawn from what is registered rather than from the filesystem."""
        entries = self._trail.entries
        for identifier in entries.ids:
            entry = entries[identifier]
            display = self.display(entry.path, isinstance(entry, Dir))
            if display.startswith(word):
                yield Completion(display, start_position=-len(word))

    def entry(self, token: str) -> Entry | None:
        """
        The registered entry an argument names, by hexadecimal id or by path. An id may be given
        shortened, since that is the only way one is ever displayed; a leading `#` forces the
        token to be read as one, for a resource whose name would otherwise shadow it.
        """
        entries = self._trail.entries
        if token.startswith("#"):
            return self.identified(token[1:])
        found = entries.get(token)
        if found is not None:
            return found
        found = entries.get(self.absolute(Path(token).expanduser()))
        if found is not None:
            return found
        return self.identified(token)

    def identified(self, prefix: str) -> Entry | None:
        """The one registered entry whose id starts with `prefix`, reporting a tie rather than
        resolving it arbitrarily."""
        entries = self._trail.entries
        matches = [
            identifier
            for identifier in entries.ids
            if identifier.startswith(prefix)
        ]
        if len(matches) > 1:
            shown = ", ".join(
                f"#{identifier[:8]}"
                for identifier in matches
            )
            self._feed.error(f"ambiguous id {prefix!r}: {shown}")
            return None
        if not matches:
            return None
        return entries[matches[0]]

    def paths(
        self,
        arguments: Sequence[str],
        tracked: bool = False,
    ) -> list[Path]:
        """
        Resolves the arguments of a command against the project root. A token carrying glob magic
        is matched against the filesystem, or against the registered paths when `tracked`, so that
        a resource that has already been deleted can still be named.
        """
        selected: dict[Path, None] = {}
        for token in arguments:
            expanded = Path(token).expanduser()
            if not any(char in token for char in MAGIC):
                selected[self.absolute(expanded)] = None
                continue
            if tracked:
                matches = self.registered(expanded)
            else:
                matches = sorted(
                    self.absolute(Path(match))
                    for match in iglob(str(expanded), root_dir=self._console.root, recursive=True)
                )
            if not matches:
                self._feed.error(f"no matches: {token}")
            for match in matches:
                selected[match] = None
        return list(selected)

    def registered(self, pattern: Path) -> list[Path]:
        entries = self._trail.entries
        expanded = str(self.absolute(pattern))
        out = sorted(
            entries[identifier].path
            for identifier in entries.ids
            if fnmatch(str(entries[identifier].path), expanded)
        )
        return out

    def confirmed(
        self,
        arguments: Sequence[str],
        command: str,
        subject: str,
    ) -> bool:
        """
        Whether an action that discards part of the record may go ahead. Unconfirmed, it reports
        what would be lost instead of losing it; the log is append-only everywhere else, so
        nothing else in the console can take something back out of it.
        """
        unexpected = [
            token
            for token in arguments
            if token not in FORCE
        ]
        if unexpected:
            self._feed.error(f"usage: {command} [-f]")
            return False
        if not arguments:
            self._feed.info(f"{subject}; '{command} -f' to confirm")
            return False
        return True

    def absolute(self, path: Path) -> Path:
        if not path.is_absolute():
            path = self._console.root / path
        return path.resolve()


SLICE = re.compile(r"(-?\d+)?:(-?\d+)?")
COUNT = re.compile(r"\d+")


class Listing(Command):
    """
    A command that selects out of one of the Trail's collections and prints what it finds the way
    the feed prints an event. A subclass says what it lists and which of its fields may be named;
    the grammar and the output are the same for all of them, so a new listing is a declaration:

        events                        the last `default` records
        events :5    -5:    2:7       a slice of them, counted the way Python counts
        events entry='c9f380c2'       only those whose field holds that value
        events 'c9f380c2'             the fields tried in turn, to paste back what was shown

    A bare value is what makes the last form work: the record's own id, then the id of the
    resource it was recorded against, then the paths, so whatever was copied out of a block
    finds the thing it came from without being told which field it was.

    Arguments pair up. Values naming the one field read as alternatives, values naming
    different fields all have to hold, and the slices cut whatever the values left, in the
    order they were written:

        assets path/to/asset -5:      the last five of what that path left
        events a.csv b.csv            either file's records
        events created a.csv -2:      two fields, so both hold, and then the last two
    """

    # the fields `field=value` may name, each with the kind of match it takes, in the order a
    # bare value is tried against them
    fields: ClassVar[dict[str, str]] = {}
    # records shown when the arguments do not say how many
    default = 20

    def items(self) -> Sequence[Repr]:
        """The collection this command lists, in the order it is held."""
        raise NotImplementedError

    def held(self, item: Repr, name: str) -> object | None:
        """What `item` keeps in the named field, or None when it keeps nothing there."""
        return getattr(item, name, None)

    def clear(self) -> None:
        """Empties the collection this command lists."""
        raise NotImplementedError

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        word = self.word(document)
        if "=" in word:
            return
        tokens = document.text_before_cursor.split()
        first = len(tokens) <= 1 or (len(tokens) == 2 and word)
        # offered only where it is the action, and not where it would be a value to match
        if first and CLEAR.startswith(word):
            yield Completion(CLEAR, start_position=-len(word))
        for name in self.fields:
            if name.startswith(word):
                yield Completion(f"{name}=", start_position=-len(word))

    def __call__(self, arguments: Sequence[str]) -> None:
        if arguments and arguments[0] == CLEAR:
            self.discard(arguments[1:])
            return
        items = list(self.items())
        selected = self.select(items, arguments)
        if selected is None:
            return
        if not selected:
            self._feed.info(f"{self.name}: nothing matched")
            return
        self._feed.info(f"{self.name} ({len(selected)} of {len(items)})")
        renderer = self._renderer
        for position, item in selected:
            self._feed.extend(renderer.record(item, position))

    def discard(self, arguments: Sequence[str]) -> None:
        """
        `clear` on a listing. What it holds is counted first, since what is about to go is the
        only thing it can report once it has gone.
        """
        total = len(self.items())
        if not total:
            self._feed.info(f"{self.name}: already empty")
            return
        subject = f"{self.name}: this clears {total}"
        if not self.confirmed(arguments, f"{self.name} {CLEAR}", subject):
            return
        self.clear()
        self._feed.info(f"{self.name}: cleared {total}")

    def select(
        self,
        items: Sequence[Repr],
        arguments: Sequence[str],
    ) -> list[tuple[int, Repr]] | None:
        """
        The records the arguments pick out, each with the position it holds in the whole
        collection, so that a slice or a filter prints the number the collection addresses it by
        rather than the number it happens to have among the results.

        The values are gathered by the field they name before any of them is applied, which is
        what lets two of them read as alternatives: the second is looked up against everything
        the first would have thrown away.
        """
        pairs = list(enumerate(items))
        wanted: dict[str, list[str]] = {}
        cuts: list[slice] = []
        for token in arguments:
            bounds = self.cut(token)
            if bounds is not None:
                cuts.append(bounds)
                continue
            named = self.named(token, pairs)
            if named is None:
                return None
            name, value = named
            wanted.setdefault(name, []).append(value)
        pairs = self.narrow(pairs, wanted)
        if not cuts:
            cuts = [slice(-self.default, None)]
        for bounds in cuts:
            pairs = pairs[bounds]
        return pairs

    def cut(self, token: str) -> slice | None:
        """The slice a token asks for, or None when it is not asking for one."""
        if SLICE.fullmatch(token):
            return self.bounds(token)
        if COUNT.fullmatch(token):
            return slice(-int(token), None)
        return None

    def named(
        self,
        token: str,
        pairs: list[tuple[int, Repr]],
    ) -> tuple[str, str] | None:
        """
        The field a token filters on and the value it filters by. A bare value names no field,
        so the fields are tried in turn and the first that holds it anywhere answers.
        """
        divider = token.find("=")
        if divider > 0:
            name = token[:divider]
            if name not in self.fields:
                fields = ", ".join(self.fields)
                self._feed.error(f"{self.name}: no field {name!r}; it has {fields}")
                return None
            return name, token[divider + 1:]
        for name in self.fields:
            held = any(
                self.matches(item, name, token)
                for _, item in pairs
            )
            if held:
                return name, token
        self._feed.error(f"{self.name}: no record holds {token!r}")
        return None

    def narrow(
        self,
        pairs: list[tuple[int, Repr]],
        wanted: dict[str, list[str]],
    ) -> list[tuple[int, Repr]]:
        """Each field the values named has to hold one of them for a record to be kept."""
        for name, values in wanted.items():
            pairs = [
                (position, item)
                for position, item in pairs
                if any(
                    self.matches(item, name, value)
                    for value in values
                )
            ]
        return pairs

    def matches(
        self,
        item: Repr,
        name: str,
        value: str,
    ) -> bool:
        held = self.held(item, name)
        if held is None or not value:
            return False
        kind = self.fields[name]
        if kind == "id":
            # ids are only ever shown shortened, so a prefix is what there is to paste back
            return str(held).startswith(value.lstrip("#").lower())
        if kind == "path":
            if str(held) == value:
                return True
            return Path(held) == self.absolute(Path(value).expanduser())
        return str(held) == value

    @staticmethod
    def bounds(token: str) -> slice:
        parts = token.split(":")
        if parts[0]:
            first = int(parts[0])
        else:
            first = None
        if parts[1]:
            last = int(parts[1])
        else:
            last = None
        return slice(first, last)


class CommandCompleter(Completer):
    """Completes the verb at the head of the line, then hands the rest to that command."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        head = document.text_before_cursor.lstrip()
        if " " not in head:
            for name in self.console.commands:
                if name.startswith(head):
                    yield Completion(name, start_position=-len(head))
            return
        command = self.console.commands.find(head.split(" ", 1)[0])
        if command is None:
            return
        yield from command.complete(document, complete_event)
