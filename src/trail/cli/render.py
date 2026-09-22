from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import StyleAndTextTuples

from trail.cli.node import Node
from trail.cli.theme import INDENT, VERB_STYLES
from trail.dir import Dir
from trail.event import AddEntryEvent, Event, RemoveEntryEvent, WatchdogEvent
from trail.util import Repr, bare_repr

if TYPE_CHECKING:
    from trail.cli.console import Console


class Renderer(Node):
    """
    Turns the Trail's objects into feed rows. The wording it picks for an event, such as the
    verb, is presentation only. It is kept here so that the widgets do not own it and a later
    provenance export can word the same things its own way.
    It holds no state; the feed tracks its own date.
    """

    _parent: Console

    @property
    def root(self) -> Path:
        return self._console.root

    @staticmethod
    def day_row(
        local: datetime,
        pattern: str,
    ) -> StyleAndTextTuples:
        """The date row the feed writes when the events it is appending cross into another day."""
        return [("class:day", f"{local:{pattern}}")]

    def record(
        self,
        item: Repr,
        position: int,
    ) -> list[StyleAndTextTuples]:
        """
        Any object the Trail reprs, laid out the way its own repr lays it out: the class name,
        then one line per field. Events and Entries both answer `_repr_items`, so a listing of
        either reads like the feed, and a class that gains a field gains a line here for free.
        """
        out = [self.heading(item, position)]
        out.extend(
            self.parameter(name, value)
            for name, value in self.parameters(item)
        )
        return out

    def render(
        self,
        event: Event,
        position: int,
    ) -> list[StyleAndTextTuples]:
        return self.record(event, position)

    def heading(
        self,
        item: Repr,
        position: int,
    ) -> StyleAndTextTuples:
        """
        Where the record sits in the log, and its class name. The class name is coloured by what
        happened, so a deletion reads differently from a creation without the heading spelling
        it out; `event_type` does that exactly, one line below.
        """
        out: StyleAndTextTuples = [
            ("class:position", f"{position}. "),
            (self.style(item), type(item).__name__),
        ]
        return out

    def style(self, item: Repr) -> str:
        """A record is coloured by what it says happened; one that says nothing is left plain."""
        if isinstance(item, Event):
            return VERB_STYLES.get(self.verb(item), "class:event")
        return "class:kind"

    def parameters(self, item: Repr) -> Iterator[tuple[str, str]]:
        """Every field the event reprs, then the resource it was recorded against."""
        for name, value in item._repr_items():
            yield name, self.value(name, value)
        # the repr leaves the resource out, but the stored record keeps it, and that id is the
        # identity the whole project turns on, so the feed states it last
        entry = getattr(item, "entry", None)
        if entry is None:
            return
        yield "entry", bare_repr(entry.id)

    def parameter(
        self,
        name: str,
        value: str,
    ) -> StyleAndTextTuples:
        return [
            ("class:parameter", f"{INDENT}{name}: "),
            ("", value),
        ]

    def value(
        self,
        name: str,
        value: object,
    ) -> str:
        """A field worded for a reader: a path against the project, anything else as it reprs."""
        if name.endswith("path"):
            value = self.display(value)
        return bare_repr(value)

    def header(self) -> StyleAndTextTuples:
        trail = self._trail
        if trail.watchdog.running:
            state = "watching"
        else:
            state = "idle"
        row: StyleAndTextTuples = [
            ("class:header.name", " trail "),
            ("class:header.id", f"#{trail.id}  "),
            ("", self.home(self.root)),
        ]
        if trail.dir is None:
            row.append(("class:header.mode", "  (nodir)"))
        counts = (
            f"  assets {len(trail.assets)}"
            f"  dirs {len(trail.dirs)}"
            f"  events {len(trail.events)}"
            f"  {state}"
        )
        row.append(("class:header.counts", counts))
        return row

    def display(
        self,
        path: str | Path,
        directory: bool = False,
    ) -> str:
        path = Path(path)
        try:
            text = str(path.relative_to(self.root))
        except ValueError:
            text = str(path)
        if directory:
            text += "/"
        return text

    @staticmethod
    def verb(event: Event) -> str:
        if isinstance(event, AddEntryEvent):
            return "tracked"
        if isinstance(event, RemoveEntryEvent):
            return "offtrailed"
        if isinstance(event, WatchdogEvent):
            # a synthetic creation is a resource the walk of a new directory turned up
            if event.is_synthetic and event.event_type == "created":
                return "discovered"
            return event.event_type or "event"
        return type(event).__name__.removesuffix("Event").lower()

    @staticmethod
    def directory(event: Event) -> bool:
        if getattr(event, "is_directory", False):
            return True
        return isinstance(event.entry, Dir)

    @staticmethod
    def home(path: Path) -> str:
        try:
            return f"~/{path.relative_to(Path.home())}"
        except (RuntimeError, ValueError):
            return str(path)
