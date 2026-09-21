from __future__ import annotations

import os
from base64 import b64encode
from collections.abc import Callable, Iterator, Sequence
from functools import cached_property
from pathlib import Path
from tempfile import gettempdir
from typing import TYPE_CHECKING, ClassVar, Final

from prompt_toolkit.completion import CompleteEvent, Completion, PathCompleter
from prompt_toolkit.document import Document

from trail.cli.command import FORCE, Command, Listing
from trail.cli.node import Node
from trail.dir import Dir
from trail.entry import Entries
from trail.util import Repr

if TYPE_CHECKING:
    from trail.cli.column import Column
    from trail.cli.console import Console

KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("enter", "run the command"),
    ("tab", "complete a command or a path"),
    ("up / down", "command history"),
    ("pageup / pagedown", "scroll the feed"),
    ("wheel", "scroll the feed or column under the pointer"),
    ("ctrl-o", "release the mouse to the terminal, or take it back"),
    ("ctrl-l", "empty the terminal, scrollback and all; nothing tracked is touched"),
    ("ctrl-c", "quit"),
)

NOTES: Final[tuple[str, ...]] = (
    "registering a directory tracks the directory itself: files created inside it",
    "afterwards are picked up automatically, files already inside it are not",
    "a column follows one resource, or everything under it when it is a directory,",
    "and keeps the history it gathered even once the resource is unregistered",
    "the wheel is read by the session, so the terminal's own selection needs shift",
    "a listing pairs its arguments: the one field twice reads as either, two fields both",
    "have to hold, and a slice cuts what they left, so 'events a.csv b.csv -5:' is the",
    "last five records of either file",
    "every listing takes 'clear': 'assets clear -f' unregisters what it lists and",
    "records the removals, while 'events clear -f' discards the log those were kept in",
    "'clear -f' on its own does both, and ctrl-l empties the terminal instead",
    "'restart' reopens the project in a new process, which is how an edit to the",
    "console's own source takes effect without losing what was recorded",
)

# the least `help` indents its descriptions by, whatever it is listing
HELP_WIDTH: Final = 20


class RegisterCommand(Command):
    name = "register"
    usage = "register PATH..."
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
                trail.register(path)
            except (OSError, ValueError) as error:
                self._feed.error(f"{self.display(path)}: {error}")


class UnregisterCommand(Command):
    name = "unregister"
    usage = "unregister PATH..."
    summary = "stop tracking; later events for the path are ignored"

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Only a tracked path can be unregistered, so only tracked paths are offered."""
        yield from self.tracked(self.word(document))

    def __call__(self, arguments: Sequence[str]) -> None:
        if not arguments:
            self._feed.error(f"usage: {self.usage}")
            return
        trail = self._trail
        for path in self.paths(arguments, tracked=True):
            if path not in trail.entries:
                self._feed.error(f"not tracked: {self.display(path)}")
                continue
            trail.unregister(path)


class ColumnCommand(Command):
    """
    `column` addresses the columns by the position drawn in their heading, counting from the
    first column rather than from the feed, which is not one of them and cannot be moved.
    """

    name = "column"
    usage = "column add|remove|move"
    summary = "open a feed narrowed to one resource, or rearrange the open ones"

    @cached_property
    def actions(self) -> dict[str, Callable[[Sequence[str]], None]]:
        return {
            "add": self._add,
            "remove": self._remove,
            "move": self._move,
            "list": self._list,
        }

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        word = self.word(document)
        tokens = document.text_before_cursor.split()
        # the action is the token after the verb; what follows it names a tracked resource
        if len(tokens) <= 1 or (len(tokens) == 2 and word):
            for action in self.actions:
                if action.startswith(word):
                    yield Completion(action, start_position=-len(word))
            return
        if self._action(tokens[1]) in ("add", "remove"):
            yield from self.tracked(word)

    def _action(self, token: str) -> str | None:
        if token in self.actions:
            return token
        matches = sorted(
            key
            for key in self.actions
            if key.startswith(token)
        )
        if len(matches) == 1:
            return matches[0]
        return None

    def __call__(self, arguments: Sequence[str]) -> None:
        if not self._console.panes:
            self._feed.error(
                "columns are off: they need the alternate screen, which costs the terminal "
                "its scrollback and its selection"
            )
            return
        if not arguments:
            self._list(())
            return
        action = self._action(arguments[0])
        if action is None:
            self._feed.error(f"usage: column {'|'.join(self.actions)}")
            return
        self.actions[action](arguments[1:])

    def _add(self, arguments: Sequence[str]) -> None:
        if not arguments:
            self._feed.error("usage: column add PATH|ID")
            return
        columns = self._console.columns
        for token in arguments:
            entry = self.entry(token)
            if entry is None:
                self._feed.error(f"not tracked: {token}")
                continue
            column = columns.get(entry)
            if column is not None:
                self._feed.info(f"column {column.position} already follows {entry.name}")
                continue
            column = columns.add(entry)
            display = self.display(entry.path, isinstance(entry, Dir))
            self._feed.info(f"column {column.position}: {display}")

    def _remove(self, arguments: Sequence[str]) -> None:
        if not arguments:
            self._feed.error("usage: column remove POSITION|PATH|ID")
            return
        columns = self._console.columns
        for token in arguments:
            column = self._column(token)
            if column is None:
                self._feed.error(f"no column for {token!r}")
                continue
            position = column.position
            entry = column.entry
            columns.remove(column)
            display = self.display(entry.path, isinstance(entry, Dir))
            self._feed.info(f"closed column {position}: {display}")

    def _move(self, arguments: Sequence[str]) -> None:
        if len(arguments) != 2:
            self._feed.error("usage: column move FROM TO")
            return
        try:
            source = int(arguments[0])
            destination = int(arguments[1])
        except ValueError:
            self._feed.error(f"expected two positions, got {' '.join(arguments)!r}")
            return
        columns = self._console.columns
        # bounds are checked here so that a negative position cannot quietly wrap around the list
        if (
            not 0 <= source < len(columns)
            or not 0 <= destination < len(columns)
        ):
            self._feed.error(f"no column at {source} to move to {destination}")
            return
        column = columns.move(source, destination)
        self._feed.info(f"column {source} moved to {column.position}")

    def _list(self, arguments: Sequence[str]) -> None:
        columns = self._console.columns
        if not len(columns):
            self._feed.info("no columns; try 'column add PATH'")
            return
        for column in columns:
            entry = column.entry
            self._feed.append(
                [
                    ("class:id", f"  {column.position}  #{entry.id[:8]}  "),
                    ("", self.display(entry.path, isinstance(entry, Dir))),
                ]
            )

    def _column(self, token: str) -> Column | None:
        columns = self._console.columns
        if token.isdigit():
            position = int(token)
            if 0 <= position < len(columns):
                return columns[position]
            return None
        entry = self.entry(token)
        if entry is None:
            return None
        return columns.get(entry)


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
        Unregisters everything listed. The removals are recorded like any other, so what was
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
        Discards the log. What is registered is left alone for this session, but nothing records
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


class CopyCommand(Command):
    """
    Takes a pane's text out of the application rather than off the screen. Dragging a selection
    asks the terminal to hold one across a repaint, which a feed that redraws on every event does
    not let it do, so what is copied here is what the pane holds rather than what survived.
    """

    name = "copy"
    usage = "copy [POSITION]"
    summary = "put a pane's text on the clipboard, and beside the log in case that fails"
    # characters sent in one OSC 52 write, under what terminals accept before truncating
    limit = 100_000

    def __call__(self, arguments: Sequence[str]) -> None:
        pane = self._feed
        label = "feed"
        if arguments:
            column = self._console.columns
            position = arguments[0]
            if not position.isdigit() or not 0 <= int(position) < len(column):
                self._feed.error(f"no column at {position!r}")
                return
            pane = column[int(position)]
            label = f"column {position}"
        text = "\n".join(
            "".join(fragment[1] for fragment in row)
            for row in pane.rows
        )
        if not text:
            self._feed.info(f"the {label} is empty")
            return
        sent = text[-self.limit:]
        self.clipboard(sent)
        path = self.beside(text)
        self._feed.info(f"copied {len(pane.rows)} rows of the {label} to the clipboard")
        self._feed.info(f"and wrote {self.display(path)}, for a terminal that ignored it")

    def clipboard(self, text: str) -> None:
        """
        OSC 52, which reaches the clipboard of the terminal the user is actually sitting at, even
        across ssh. A multiplexer forwards it only when asked, and only with its own escape
        doubled, so it is wrapped for one when the session is inside it.
        """
        payload = b64encode(text.encode("utf-8")).decode("ascii")
        sequence = f"\x1b]52;c;{payload}\x07"
        if os.environ.get("TMUX"):
            sequence = f"\x1bPtmux;{sequence.replace(chr(27), chr(27) * 2)}\x1b\\"
        output = self._console.application.output
        output.write_raw(sequence)
        output.flush()

    def beside(self, text: str) -> Path:
        """
        The same text as a file, because no escape sequence works everywhere. It is written into
        the metadata directory, which the Trail ignores, so copying does not record an event.
        """
        trail = self._trail
        if trail.dir is None:
            path = Path(gettempdir()) / f"trail-{trail.id[:8]}-copy.txt"
        else:
            path = trail.dir / "copy.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class MouseCommand(Command):
    """
    The wheel reaches a pane only while the terminal reports the mouse to the application, and
    that same reporting is what stops a drag from selecting text. Releasing it hands selection
    and middle-click paste back to the terminal until it is taken again.
    """

    name = "mouse"
    usage = "mouse [on|off]"
    summary = "hold the mouse for the wheel, or release it to the terminal to copy"
    states: ClassVar[dict[str, bool]] = {"on": True, "off": False}

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        word = self.word(document)
        for state in self.states:
            if state.startswith(word):
                yield Completion(state, start_position=-len(word))

    def __call__(self, arguments: Sequence[str]) -> None:
        if not self._console.panes:
            self._feed.info("the terminal already holds the mouse; nothing here reports it")
            return
        if not arguments:
            self._console.mouse = not self._console.mouse
            return
        if arguments[0] not in self.states:
            self._feed.error(f"usage: {self.usage}")
            return
        self._console.mouse = self.states[arguments[0]]


class ClearCommand(Command):
    """
    The whole project record at once: what `entries clear` and `events clear` do together. It is
    the one command that takes something back out of an otherwise append-only record, so it says
    what would go and waits to be told again.
    """

    name = "clear"
    usage = "clear [-f]"
    summary = "discard the project record: unregister everything, then empty the log"

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
    session comes back holding what it held, minus the command history and the open columns.
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
