from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from functools import cached_property
from typing import TYPE_CHECKING

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from prompt_toolkit.layout import (
    BufferControl,
    Container,
    Dimension,
    FormattedTextControl,
    HSplit,
    ScrollbarMargin,
    Window,
)
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.shortcuts import print_formatted_text

from trail.cli.node import Node
from trail.cli.theme import PROMPT, STYLE
from trail.event import Event

if TYPE_CHECKING:
    from prompt_toolkit.key_binding.key_bindings import NotImplementedOrNone



class FeedLexer(Lexer):
    """
    Hands the renderer the fragments a row was appended with. The buffer holds only the plain
    text, so that scrolling, wrapping and selection come for free; the colour of a row lives
    here instead, indexed by the line number the buffer assigned it.
    """

    def __init__(self, feed: Feed) -> None:
        self.feed = feed

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        rows = self.feed.rows

        def get_line(lineno: int) -> StyleAndTextTuples:
            if 0 <= lineno < len(rows):
                return rows[lineno]
            return []

        return get_line


class FeedWindow(Window):
    """
    Scrolls the wheel by moving the buffer cursor rather than the window's own offset, because
    the feed follows that cursor to stay at the tail; an offset moved on its own would be undone
    by the next render.
    """

    # rows one notch of the wheel travels
    wheel = 3

    def __init__(
        self,
        feed: Feed,
        **kwargs,
    ) -> None:
        Window.__init__(self, **kwargs)
        self.feed = feed

    def _mouse_handler(self, mouse_event: MouseEvent) -> NotImplementedOrNone:
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.feed.scroll(-self.wheel)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.feed.scroll(self.wheel)
            return None
        return NotImplemented


class Feed(Node):
    """
    The scrollback of one pane, whether the whole log or a column. Rows arrive already rendered,
    are appended and never rewritten, and the oldest are dropped once `limit` is exceeded, so a
    session that runs for days stays bounded.
    """

    # rows retained before the head of the feed is discarded
    limit = 2000
    # rows scrolled per page before the window has been rendered once
    page_fallback = 10
    # the share of the width this pane claims of the row it is laid out in
    weight = 2
    min_width = 24
    # the date of the last row written, so that a change of day announces itself once
    day: date | None = None
    # how that change is announced, at the width this pane is laid out in
    day_format = "%A %d %B %Y"

    @cached_property
    def rows(self) -> list[StyleAndTextTuples]:
        return []

    @cached_property
    def buffer(self) -> Buffer:
        return Buffer(read_only=True)

    @cached_property
    def area(self) -> FeedWindow:
        return FeedWindow(
            self,
            content=BufferControl(
                buffer=self.buffer,
                lexer=FeedLexer(self),
                focusable=False,
            ),
            wrap_lines=True,
            right_margins=[ScrollbarMargin()],
        )

    @cached_property
    def container(self) -> Container:
        return HSplit(
            [
                Window(
                    FormattedTextControl(self.header),
                    height=1,
                    style="class:column",
                ),
                self.area,
            ],
            width=Dimension(weight=self.weight, min=self.min_width),
        )

    @property
    def page(self) -> int:
        info = self.area.render_info
        if info is None:
            return self.page_fallback
        return max(1, info.window_height - 1)

    def header(self) -> StyleAndTextTuples:
        return [("class:column.title", " all events")]

    def accepts(self, event: Event) -> bool:
        return True

    def render(
        self,
        event: Event,
        position: int,
    ) -> list[StyleAndTextTuples]:
        """The lines this pane words an event in; a narrowed one restates less of it."""
        return self._renderer.render(event, position)

    def write(
        self,
        events: Iterable[Event],
        start: int = 0,
    ) -> None:
        """
        Append whichever of `events` this feed accepts, dating them as the day turns over.
        `start` is where the batch sits in the log, so that the position a pane prints against a
        record is the one `events` and `by_pos` address it by, gaps and all.
        """
        rows: list[StyleAndTextTuples] = []
        for position, event in enumerate(events, start):
            if not self.accepts(event):
                continue
            local = event.timestamp.astimezone()
            if local.date() != self.day:
                self.day = local.date()
                rows.append(self._renderer.day_row(local, self.day_format))
            rows.extend(self.render(event, position))
        self.extend(rows)

    def extend(self, rows: Iterable[StyleAndTextTuples]) -> None:
        appended = [
            [
                (fragment[0], fragment[1].replace("\n", " ").replace("\t", "    "))
                for fragment in row
            ]
            for row in rows
        ]
        if not appended:
            return
        self.rows.extend(appended)
        overflow = len(self.rows) - self.limit
        if overflow > 0:
            del self.rows[:overflow]
        self.show(appended)

    def show(self, rows: list[StyleAndTextTuples]) -> None:
        """
        Hand the rows to the terminal as ordinary output. Nothing redraws them afterwards, so the
        terminal keeps its own scrollback, selection and scrollbar over them, which is the whole
        reason the console stays off the alternate screen. A pane owns a region that is repainted
        and can have none of that, so it fills its buffer instead.
        """
        if not self._console.panes:
            # one write for the whole batch: the bar below is erased and redrawn around each
            # one, so a burst of events costs a single redraw rather than one per line
            fragments: StyleAndTextTuples = []
            for row in rows:
                if fragments:
                    fragments.append(("", "\n"))
                fragments.extend(row)
            print_formatted_text(FormattedText(fragments), style=STYLE)
            return
        buffer = self.buffer
        document = buffer.document
        # a cursor on the last row pins the window to the tail; anywhere above it means the
        # reader scrolled back and the viewport is left where they put it. It is the row and
        # not the offset, because scrolling down keeps the column and so stops short of the end
        following = document.cursor_position_row >= document.line_count - 1
        text = "\n".join(
            "".join(fragment[1] for fragment in row)
            for row in self.rows
        )
        if following:
            position = len(text)
        else:
            position = min(buffer.cursor_position, len(text))
        buffer.set_document(Document(text, position), bypass_readonly=True)

    def append(self, row: StyleAndTextTuples) -> None:
        self.extend([row])

    def echo(self, text: str) -> None:
        self.append([("class:echo.prompt", PROMPT), ("class:echo", text)])

    def info(self, text: str) -> None:
        self.append([("class:info", text)])

    def error(self, text: str) -> None:
        self.append([("class:error", text)])

    def clear(self) -> None:
        self.rows.clear()
        self.day = None
        self.buffer.set_document(Document(), bypass_readonly=True)

    def scroll(self, rows: int) -> None:
        buffer = self.buffer
        if rows < 0:
            buffer.cursor_up(-rows)
        else:
            buffer.cursor_down(rows)
