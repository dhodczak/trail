from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import StyleAndTextTuples

from trail.cli.node import Node
from trail.cli.theme import INDENT, VERB_STYLES
from trail.dir import Dir
from trail.event import AddEntryEvent, Event, RemoveEntryEvent, WatchdogEvent
from trail.util import Repr

if TYPE_CHECKING:
    from trail.cli.console import Console
    from trail.entry import Entry


class Renderer(Node):
    """
    Turns the Trail's own objects into feed rows. The vocabulary it assigns an event -- the verb,
    the shortened resource id, the arrow of a move -- is the presentation a later provenance
    export would restate, so it is kept out of the widgets that happen to display it now. It
    holds no state of its own: every feed dates its own rows, since each one skips different days.
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
        """
        The rule a feed writes when the events it is appending cross into another day. The
        pattern comes from the pane, because a column is narrower than the spelled-out date.
        """
        return [("class:day", f"{local:{pattern}}")]

    def record(
        self,
        item: Repr,
        position: int,
        pane: Entry | None = None,
        omit: Sequence[str] = (),
    ) -> list[StyleAndTextTuples]:
        """
        Anything the Trail reprs, worded as its own repr words it: the record's class, then a
        line for each field it carries. An Event and an Entry both answer `_repr_items`, so a
        listing of either reads the same way the feed does, and a class that gains a field gains
        a line without this being told.
        """
        out = [self.heading(item, position)]
        out.extend(
            self.parameter(name, value)
            for name, value in self.parameters(item, pane, omit)
        )
        return out

    def render(
        self,
        event: Event,
        position: int,
    ) -> list[StyleAndTextTuples]:
        return self.record(event, position)

    def compact(
        self,
        event: Event,
        entry: Entry,
        position: int,
    ) -> list[StyleAndTextTuples]:
        """
        The same block for a pane narrowed to `entry`, which is titled with the resource and is
        not given the feed's width: the path it is headed by is not restated, and the record's
        own id is left to the feed.
        """
        return self.record(event, position, pane=entry, omit=("id",))

    def heading(
        self,
        item: Repr,
        position: int,
    ) -> StyleAndTextTuples:
        """
        Where the record sits in the log, and its class. The class is coloured by what the record
        says happened, so a deletion reads differently from a creation without the heading saying
        so twice: `event_type` states it exactly, a line below.
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

    def parameters(
        self,
        item: Repr,
        pane: Entry | None = None,
        omit: Sequence[str] = (),
    ) -> Iterator[tuple[str, str]]:
        """
        Every field the event puts in its own repr, then the resource it was recorded against.
        `pane` is the resource a pane is headed by: paths are named against it rather than
        against the project, and a field naming that resource is dropped, the title being it.
        """
        for name, value in item._repr_items():
            if name in omit:
                continue
            if (
                name.endswith("_path")
                and pane is not None
                and Path(value) == pane.path
            ):
                continue
            yield name, self.value(name, value, pane)
        # the repr leaves the resource out, but the stored record keeps it and it is the
        # identity the whole project turns on, so the feed states it last
        entry = getattr(item, "entry", None)
        if (
            entry is None
            or "entry" in omit
            or (pane is not None and entry.id == pane.id)
        ):
            return
        yield "entry", repr(entry.id)

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
        pane: Entry | None = None,
    ) -> str:
        """A field worded for a reader: a path against its pane, an mtime as a wall clock."""
        if name.endswith("path"):
            if pane is None:
                return repr(self.display(value))
            return repr(self.within(value, pane.path))
        if name == "mtime" and isinstance(value, (int, float)):
            moment = datetime.fromtimestamp(value, UTC).astimezone()
            return repr(f"{moment:%Y-%m-%d %H:%M:%S}")
        return repr(value)

    @staticmethod
    def within(
        path: str | Path,
        base: Path,
    ) -> str:
        """A descendant named relative to the resource whose pane it appears in."""
        path = Path(path)
        try:
            return str(path.relative_to(base))
        except ValueError:
            return path.name

    def header(self) -> StyleAndTextTuples:
        trail = self._trail
        if trail.watchdog.running:
            state = "watching"
        else:
            state = "idle"
        row: StyleAndTextTuples = [
            ("class:header.name", " trail "),
            ("class:header.id", f"#{trail.id[:8]}  "),
            ("", self.home(self.root)),
        ]
        if trail.dir is None:
            row.append(("class:header.mode", "  (nodir)"))
        if not self._console.mouse:
            row.append(("class:header.mode", "  (select)"))
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
            return "registered"
        if isinstance(event, RemoveEntryEvent):
            return "unregistered"
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
