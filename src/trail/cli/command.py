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
# the action that empties a listing; it has to be spelled in full, since a bare value is
# read as something to search for
CLEAR = "clear"


class Command(Node):
    """
    One verb of the command bar. Every subclass adds itself to `classes` under its `name`, so
    adding a command means writing a class and nothing else. The dispatch table, the help text
    and the argument completion are all read back off `classes`.
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
        """How this CLI words a path; the feed words it the same way."""
        return self._renderer.display(path, directory)

    def word(self, document: Document) -> str:
        """The argument being completed, as a document of its own for a nested completer."""
        return document.get_word_before_cursor(WORD=True)

    def offer(self, word: str) -> Iterator[Completion]:
        """Completions drawn from what is tracked rather than from the filesystem."""
        entries = self._trail.entries
        for identifier in entries.ids:
            entry = entries[identifier]
            display = self.display(entry.path, isinstance(entry, Dir))
            if display.startswith(word):
                yield Completion(display, start_position=-len(word))

    def entry(self, token: str) -> Entry | None:
        """
        The tracked entry an argument names, by id or by path. A leading `#` forces the token to
        be read as an id, for the case where a file's name would otherwise match first.
        """
        entries = self._trail.entries
        if token.startswith("#"):
            return entries.id2entry.get(token[1:])
        found = entries.get(token)
        if found is not None:
            return found
        return entries.get(self.absolute(Path(token).expanduser()))

    def paths(
        self,
        arguments: Sequence[str],
        tracked: bool = False,
    ) -> list[Path]:
        """
        Resolve a command's arguments against the project root. A token holding glob characters
        is matched against the filesystem, or against the tracked paths when `tracked` is set,
        which is how a resource that has already been deleted can still be named.
        """
        selected: dict[Path, None] = {}
        for token in arguments:
            expanded = Path(token).expanduser()
            if not any(char in token for char in MAGIC):
                selected[self.absolute(expanded)] = None
                continue
            if tracked:
                matches = self.tracked(expanded)
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

    def tracked(self, pattern: Path) -> list[Path]:
        """The tracked paths a glob matches; `paths` uses this in place of the filesystem."""
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
        Whether an action that discards part of the record may go ahead. Without `-f` it reports
        what would be lost instead of losing it. The log is append-only everywhere else, so
        these are the only commands that can take anything back out of it.
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
    A command that lists one of the Trail's collections, printing each record the way the feed
    prints an event. A subclass only says what it lists and which fields may be filtered on; the
    grammar and the output are shared, so a new listing is mostly a declaration:

        events                        the last `default` records
        events :5    -5:    2:7       a slice, counted the way Python counts
        events entry='c9f380c2737c467fa0aad96d70340999'
                                      only records whose `entry` holds that value
        events 'c9f380c2737c467fa0aad96d70340999'
                                      no field named, so the fields are tried in turn

    The last form is there so that anything copied out of a printed block can be pasted
    straight back. The fields are tried in the order `fields` lists them: the record's own id,
    then the id of the resource it was recorded against, then the paths.

    Arguments combine like this. Two values for one field mean either of them, values for
    different fields all have to hold, and any slices cut what is left, in the order written:

        assets path/to/asset -5:      the last five records for that path
        events a.csv b.csv            either file's records
        events created a.csv -2:      both fields hold, then the last two
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
        `clear` on a listing. The count is taken first, because afterwards there is nothing
        left to count.
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
        The records the arguments pick out, each paired with its position in the whole
        collection. That position is the collection's own rather than the result's, so a
        filtered record still prints the number used to address it.

        Values are grouped by the field they name before any filtering runs. That is what lets
        two values for one field mean either of them: the second is matched against everything
        the first would have discarded.
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
        so the fields are tried in order and the first one that matches any record wins.
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
        """A record is kept only if every named field holds one of the values given for it."""
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
            return str(held) == value.lstrip("#").lower()
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
