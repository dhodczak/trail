from __future__ import annotations

import shlex
from collections.abc import Iterator, Sequence
from fnmatch import fnmatch
from glob import iglob
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from trail.cli.node import Node
from trail.dir import Dir
from trail.select import SLICE, Select, Selectable

if TYPE_CHECKING:
    from trail.cli.commands import Commands
    from trail.cli.console import Console
    from trail.entry import Entries, Entry
    from trail.event import Events

# the characters that make a command argument a pattern rather than a path
MAGIC = ("*", "?", "[")
# what confirms an action that discards the record rather than adding to it
FORCE = ("-f", "--force")
# the action that empties a listing in place of listing it, when it is the first word
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

    def submit(self, text: str) -> None:
        # what followed the command's name; a command that reads it as an expression of its own
        # overrides this to take it unsplit
        try:
            arguments = shlex.split(text)
        except ValueError as error:
            self._feed.error(f"unbalanced quotes: {error}")
            return
        self(arguments)

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
        for entry in self._trail.entries:
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
        expanded = str(self.absolute(pattern))
        out = sorted(
            entry.path
            for entry in self._trail.entries
            if fnmatch(str(entry.path), expanded)
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


class Query[T: Selectable[Any], V](Select[T, V]):
    _parent: T

    def __init__(
        self,
        parent: T,
        root: Path,
    ) -> None:
        Select.__init__(self, parent)
        self.root = root

    def within(self, collection: T) -> Self:
        return type(self)(collection, self.root)

    def comparison(self, item: str) -> T:
        match = self.parse(item)
        field = match["field"]
        value = match["value"]
        if (
            not field.endswith("path")
            or not value
        ):
            return super().comparison(item)
        # read against the project root, the way the console words a path, rather than against
        # the working directory; resolved once here rather than once for every record
        path = self.root / Path(value).expanduser()
        return self.where(field, match["operator"], str(path.resolve()))


class Listing(Command):
    """
    A command that lists one of the Trail's collections, printing each record the way the feed
    prints an event. A subclass only says what it lists; what follows the command's name is read
    by the collection's Select, so a listing takes whatever `events.select(...)` takes:

        events                                    the last `default` records
        events :5    -5:    2:7                   a slice, counted the way Python counts
        events src_path=a.csv -5:                 the last five of that file's records
        events src_path=a.csv or src_path=b.csv   either file's records
        events cls=WatchdogEvent not event_type=opened

    A path is read against the project root, the way a listing words it, so a value copied out
    of a printed record can be pasted straight back. When no slice says how many, only the last
    `default` of what matched are shown.
    """

    # the fields offered as completions; a record can be selected on any other it holds
    fields: ClassVar[tuple[str, ...]] = ()
    # records shown when the expression does not say how many
    default = 20

    def collection(self) -> Entries | Events:
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
        # offered only where it is the action, and not where it would be a term
        if first and CLEAR.startswith(word):
            yield Completion(CLEAR, start_position=-len(word))
        # a term that opens a group is completed past its parentheses
        stem = word.lstrip("(")
        for name in self.fields:
            if name.startswith(stem):
                yield Completion(f"{name}=", start_position=-len(stem))

    def submit(self, text: str) -> None:
        # taken unsplit, since once the console's split has dropped the quotes, the parentheses
        # of a quoted path can no longer be told from the ones grouping terms
        words = text.split()
        if (
            words
            and words[0] == CLEAR
        ):
            self.discard(words[1:])
            return
        collection = self.collection()
        query = Query(collection, self._console.root)
        try:
            selection = query(text)
        except (TypeError, ValueError) as error:
            self._feed.error(f"{self.name}: {error}")
            return
        sliced = any(
            SLICE.fullmatch(token)
            for token in Select.lex(text)
        )
        if not sliced:
            selection = selection.select[-self.default:]
        if not selection:
            self._feed.info(f"{self.name}: nothing matched")
            return
        self._feed.info(f"{self.name} ({len(selection)} of {len(collection)})")
        # the position the whole collection gives a record rather than its place among what
        # matched, since that is the number it is addressed by
        positions = {
            key: position
            for position, key in enumerate(collection.ids)
        }
        renderer = self._renderer
        for key in selection.ids:
            rows = renderer.record(selection.data[key], positions[key])
            self._feed.extend(rows)

    def discard(self, arguments: Sequence[str]) -> None:
        """
        `clear` on a listing. The count is taken first, because afterwards there is nothing
        left to count.
        """
        collection = self.collection()
        total = len(collection)
        if not total:
            self._feed.info(f"{self.name}: already empty")
            return
        subject = f"{self.name}: this clears {total}"
        if not self.confirmed(arguments, f"{self.name} {CLEAR}", subject):
            return
        collection.clear()
        self._feed.info(f"{self.name}: cleared {total}")


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
