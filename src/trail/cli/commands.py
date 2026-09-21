from __future__ import annotations

from collections.abc import Iterator, Sequence
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar, Final

from prompt_toolkit.completion import CompleteEvent, Completion, PathCompleter
from prompt_toolkit.document import Document

from trail.cli.command import FORCE, Command, Listing
from trail.cli.node import Node
from trail.entry import Entries
from trail.util import Repr

if TYPE_CHECKING:
    from trail.cli.console import Console

KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("enter", "run the command"),
    ("tab", "complete a command or a path"),
    ("up / down", "command history"),
    ("ctrl-l", "empty the terminal, scrollback and all; nothing tracked is touched"),
    ("ctrl-c", "quit"),
)

NOTES: Final[tuple[str, ...]] = (
    "tracking a directory tracks the directory itself: files created inside it",
    "afterwards are picked up automatically, files already inside it are not",
    "the feed is ordinary output, so scrolling, selecting and copying are the",
    "terminal's own and work as they do anywhere else",
    "a listing pairs its arguments: the one field twice reads as either, two fields both",
    "have to hold, and a slice cuts what they left, so 'events a.csv b.csv -5:' is the",
    "last five records of either file",
    "every listing takes 'clear': 'assets clear -f' untracks what it lists and",
    "records the removals, while 'events clear -f' discards the log those were kept in",
    "'clear -f' on its own does both, and ctrl-l empties the terminal instead",
    "'restart' reopens the project in a new process, which is how an edit to the",
    "console's own source takes effect without losing what was recorded",
)

# the least `help` indents its descriptions by, whatever it is listing
HELP_WIDTH: Final = 20


class TrackCommand(Command):
    name = "track"
    usage = "track PATH..."
    summary = "track files or directories; globs are expanded"

    @cached_property
    def completer(self) -> PathCompleter:
        return PathCompleter(
            expanduser=True,
            get_paths=lambda: [str(self._console.root)],
        )

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        word = self.word(document)
        yield from self.completer.get_completions(
            Document(word, len(word)),
            complete_event,
        )

    def __call__(self, arguments: Sequence[str]) -> None:
        if not arguments:
            self._feed.error(f"usage: {self.usage}")
            return
        trail = self._trail
        for path in self.paths(arguments):
            if path in trail.entries:
                self._feed.info(f"already tracked: {self.display(path)}")
                continue
            try:
                trail.track(path)
            except (OSError, ValueError) as error:
                self._feed.error(f"{self.display(path)}: {error}")


class UntrackCommand(Command):
    name = "untrack"
    usage = "untrack PATH..."
    summary = "stop tracking; later events for the path are ignored"

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Only a tracked path can be untracked, so only tracked paths are offered."""
        yield from self.offer(self.word(document))

    def __call__(self, arguments: Sequence[str]) -> None:
        if not arguments:
            self._feed.error(f"usage: {self.usage}")
            return
        trail = self._trail
        for path in self.paths(arguments, tracked=True):
            if path not in trail.entries:
                self._feed.error(f"not tracked: {self.display(path)}")
                continue
            trail.untrack(path)


class AssetsCommand(Listing):
    name = "assets"
    usage = "assets [SLICE] [FIELD=VALUE]"
    summary = "the tracked files, sliced or filtered"
    fields: ClassVar[dict[str, str]] = {
        "id": "id",
        "path": "path",
        "name": "text",
    }

    def collection(self) -> Entries:
        return self._trail.assets

    def items(self) -> Sequence[Repr]:
        collection = self.collection()
        return [
            collection[identifier]
            for identifier in collection.ids
        ]

    def clear(self) -> None:
        """
        Untracks everything listed. The removals are recorded like any other, so what was
        cleared stays cleared instead of coming back with the next replay of the log.
        """
        self.collection().clear()


class DirsCommand(AssetsCommand):
    name = "dirs"
    usage = "dirs [SLICE] [FIELD=VALUE]"
    summary = "the tracked directories, sliced or filtered"

    def collection(self) -> Entries:
        return self._trail.dirs


class EntriesCommand(AssetsCommand):
    name = "entries"
    usage = "entries [SLICE] [FIELD=VALUE]"
    summary = "everything tracked, files and directories alike"

    def collection(self) -> Entries:
        return self._trail.entries


class EventsCommand(Listing):
    name = "events"
    usage = "events [SLICE] [FIELD=VALUE]"
    summary = "the recorded events, sliced or filtered"
    fields: ClassVar[dict[str, str]] = {
        "id": "id",
        "entry": "id",
        "src_path": "path",
        "dest_path": "path",
        "event_type": "text",
    }

    def items(self) -> Sequence[Repr]:
        events = self._trail.events
        return [
            events[identifier]
            for identifier in events.ids
        ]

    def clear(self) -> None:
        """
        Discards the log. What is tracked is left alone for this session, but nothing records
        it any more, so the next session opens on a project that was never told about it.
        """
        self._trail.events.clear()

    def held(
        self,
        item: Repr,
        name: str,
    ) -> object | None:
        """The resource is the event's own field rather than a name it holds, so it is read out."""
        if name == "entry":
            if item.entry is None:
                return None
            return item.entry.id
        return getattr(item, name, None)


class ClearCommand(Command):
    """
    The whole project record at once: what `entries clear` and `events clear` do together. It is
    the one command that takes something back out of an otherwise append-only record, so it says
    what would go and waits to be told again.
    """

    name = "clear"
    usage = "clear [-f]"
    summary = "discard the project record: untrack everything, then empty the log"

    def __call__(self, arguments: Sequence[str]) -> None:
        trail = self._trail
        events = len(trail.events)
        entries = len(trail.entries)
        if not events and not entries:
            self._feed.info("clear: the project record is already empty")
            return
        # counted the way the header counts them, which is the way they are addressed
        subject = f"clear: this clears entries {entries}, events {events}"
        if not self.confirmed(arguments, "clear", subject):
            return
        trail.clear()
        self._feed.info(f"clear: cleared entries {entries}, events {events}")


class HelpCommand(Command):
    name = "help"
    usage = "help"
    summary = "this list"

    def __call__(self, arguments: Sequence[str]) -> None:
        feed = self._feed
        listed = self._console.commands.listed
        # the longest usage sets the column, so a command with a long one cannot run into its
        # own description the way a fixed width let it
        width = max(
            HELP_WIDTH,
            *(len(command.usage) for command in listed),
            *(len(keys) for keys, _ in KEYS),
        )
        feed.info("commands")
        for command in listed:
            feed.append(
                [
                    ("class:kind", f"  {command.usage:<{width}}  "),
                    ("class:info", command.summary),
                ]
            )
        feed.info("keys")
        for keys, description in KEYS:
            feed.append(
                [
                    ("class:kind", f"  {keys:<{width}}  "),
                    ("class:info", description),
                ]
            )
        for note in NOTES:
            feed.info(note)


class RestartCommand(Command):
    """
    Reopens the project in a new interpreter, on the command line this one was given. It is what
    picks up an edit to the console's own source: a session holds the classes it imported, so a
    command rewritten underneath it keeps running as it was read.

    What is recorded lives in PATH/.trail and is replayed on the way back up, so a dir-backed
    session comes back holding what it held, minus the command history.
    Under `--nodir` there is nothing on disk to come back to, so the log goes with the process
    and the restart has to be confirmed like any other discard.
    """

    name = "restart"
    usage = "restart [-f]"
    summary = "reopen this project in a new process, picking up edits to the console"

    def __call__(self, arguments: Sequence[str]) -> None:
        trail = self._trail
        if trail.dir is None:
            subject = f"restart: nodir, so this discards events {len(trail.events)}"
            if not self.confirmed(arguments, "restart", subject):
                return
        else:
            unexpected = [
                token
                for token in arguments
                if token not in FORCE
            ]
            if unexpected:
                self._feed.error(f"usage: {self.usage}")
                return
        self._console.restart()


class QuitCommand(Command):
    name = "quit"
    aliases = ("exit",)
    usage = "quit"
    summary = "leave the session"

    def __call__(self, arguments: Sequence[str]) -> None:
        self._console.application.exit()


class Commands(Node):
    """
    The command set of one Console, built from every registered Command subclass. It lives here
    rather than beside the base class because it can only be assembled once they are all defined.
    """

    _parent: Console

    @cached_property
    def data(self) -> dict[str, Command]:
        out: dict[str, Command] = {}
        for command_type in Command.classes.values():
            command = command_type(self)
            out[command.name] = command
            for alias in command_type.aliases:
                out[alias] = command
        return out

    @property
    def listed(self) -> list[Command]:
        """The commands in definition order, each once, however many names it answers to."""
        return list(dict.fromkeys(self.data.values()))

    def __getitem__(self, key: str) -> Command:
        return self.data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return f"{type(self).__name__} ({', '.join(self.data)})"

    def matching(self, name: str) -> list[str]:
        out = sorted(
            key
            for key in self.data
            if key.startswith(name)
        )
        return out

    def find(self, name: str) -> Command | None:
        """The command named exactly, or the only one that name is a prefix of."""
        command = self.data.get(name)
        if command is not None:
            return command
        matches = {
            self.data[key]
            for key in self.matching(name)
        }
        if len(matches) == 1:
            return matches.pop()
        return None

    def resolve(self, name: str) -> Command | None:
        """`find`, reporting into the feed when the name picks out no single command."""
        command = self.find(name)
        if command is not None:
            return command
        matches = self.matching(name)
        if matches:
            self._feed.error(f"ambiguous command {name!r}: {', '.join(matches)}")
        else:
            self._feed.error(f"unknown command {name!r}; try 'help'")
        return None
