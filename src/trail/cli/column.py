from __future__ import annotations

from collections.abc import Iterator
from functools import cached_property
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import StyleAndTextTuples

from trail.cli.feed import Feed
from trail.cli.node import Node
from trail.dir import Dir
from trail.entry import Entry
from trail.event import Event
from trail.util import items_repr

if TYPE_CHECKING:
    from trail.cli.console import Console

# NOTE: Column is currently disabled:
#   using the feature would disable scrolling and copy/paste, which are more more important

class Column(Feed):
    """
    A pane narrowed to one resource. A directory's pane takes its descendants as well, since
    most of what happens to a tracked directory happens inside it rather than to it.

    The pane holds the resource it was opened on rather than the path that named it, so a rename
    retitles the pane instead of silencing it, and unregistering leaves the history gathered so
    far in place.
    """

    _parent: Columns
    # the feed beside it carries the command output too, and is given twice the width
    weight = 1
    # the spelled-out date does not fit a column, and a replayed one may be years old
    day_format = "%Y-%m-%d"

    def __init__(
        self,
        parent: Columns,
        entry: Entry,
    ) -> None:
        Node.__init__(self, parent)
        self.entry = entry

    @property
    def position(self) -> int:
        """Where the pane sits among the panes, which is how the commands address it."""
        return self._columns.data.index(self)

    @property
    def tracked(self) -> bool:
        """False once the resource is unregistered; the pane keeps its history and falls quiet."""
        return self._trail.entries.get(self.entry.id) is self.entry

    def header(self) -> StyleAndTextTuples:
        entry = self.entry
        out: StyleAndTextTuples = [
            ("class:column.index", f" {self.position} "),
            ("class:column.title", f" {entry.name}"),
            ("class:column.id", f" #{entry.id[:8]}"),
        ]
        if not self.tracked:
            out.append(("class:missing", " untracked"))
        elif not entry.path.exists():
            out.append(("class:missing", " missing"))
        return out

    def accepts(self, event: Event) -> bool:
        entry = event.entry
        if entry is None:
            return False
        if entry.id == self.entry.id:
            return True
        if isinstance(self.entry, Dir):
            return entry.path.is_relative_to(self.entry.path)
        return False

    def render(
        self,
        event: Event,
        position: int,
    ) -> list[StyleAndTextTuples]:
        return self._renderer.compact(event, self.entry, position)

    def replay(self) -> None:
        """
        Fills a freshly opened pane with what the log already holds for its resource. The
        console's cursor bounds the replay, so an event the feed has not drained yet arrives
        with that batch rather than twice.
        """
        events = self._trail.events
        self.write(
            events[identifier]
            for identifier in events.ids[:self._console.cursor]
        )

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield "position", self.position
        yield "id", self.entry.id
        yield "path", str(self.entry.path)

    def __repr__(self) -> str:
        lines = [type(self).__name__]
        lines.extend(
            f"    {name}: {value!r}"
            for name, value in self._repr_items()
        )
        return "\n".join(lines)


class Columns(Node):
    """
    The panes the user opened, left to right. The feed is not one of them: it is the whole log
    and cannot be closed or reordered, so a position always names a pane rather than the feed.

    >>> console.columns
    Columns (1)
        0. Column
            position: 0
            id: '41d3f259a5fc4c1fa13c516cf892f56e'
            path: '/tmp/tmpbzh09nb5/folder/new.csv'
    """

    _parent: Console

    @cached_property
    def data(self) -> list[Column]:
        return []

    def add(self, entry: Entry) -> Column:
        column = Column(self, entry)
        self.data.append(column)
        # laid out before the replay, so the pane the rows land in is the one on screen
        self._console.reflow()
        column.replay()
        return column

    def remove(self, column: Column) -> Column:
        self.data.remove(column)
        self._console.reflow()
        return column

    def move(
        self,
        source: int,
        destination: int,
    ) -> Column:
        column = self.data.pop(source)
        self.data.insert(destination, column)
        self._console.reflow()
        return column

    def get(self, entry: Entry) -> Column | None:
        out = next(
            (
                column
                for column in self.data
                if column.entry.id == entry.id
            ),
            None,
        )
        return out

    def __getitem__(self, position: int) -> Column:
        return self.data[position]

    def __iter__(self) -> Iterator[Column]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return items_repr(type(self).__name__, self.data)
