from __future__ import annotations

import asyncio
import shlex
from collections.abc import AsyncIterator
from contextlib import suppress
from functools import cached_property
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    AnyContainer,
    Container,
    DynamicContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.widgets import TextArea

from trail.cli.column import Columns
from trail.cli.command import CommandCompleter
from trail.cli.commands import Commands
from trail.cli.feed import Feed
from trail.cli.node import Node
from trail.cli.render import Renderer
from trail.cli.theme import PROMPT, STYLE, VERTICAL
from trail.event import Event
from trail.trail import Trail


class Console(Node):
    """
    A full-screen session: whatever the watchdog records streams into the feed as it happens,
    while the command bar below edits the registration that produces those records. The feed on
    the left takes every event; `column add` opens another beside it, narrowed to one resource.
    """

    _parent: Trail
    # seconds between checks that the observer is still alive, which no event would announce
    heartbeat = 1.0
    # the interval redraws are coalesced over while a burst of events arrives
    interval = 0.05
    # events replayed from previous sessions that are shown on startup
    backlog = 20
    # Panes need the alternate screen, which has no scrollback and is repainted under any
    # selection the terminal is holding, so copying and scrolling stop being the terminal's to
    # do. Off, the feed is ordinary output and both work again; the column code is left intact.
    panes = False

    def __init__(
        self,
        trail: Trail,
        root: Path,
    ) -> None:
        Node.__init__(self, trail)
        self.root = root
        self.cursor = 0
        self.failed = False
        self._mouse = True
        self._body: Container | None = None

    @cached_property
    def feed(self) -> Feed:
        return Feed(self)

    @cached_property
    def columns(self) -> Columns:
        return Columns(self)

    @cached_property
    def renderer(self) -> Renderer:
        return Renderer(self)

    @cached_property
    def commands(self) -> Commands:
        return Commands(self)

    @cached_property
    def completer(self) -> CommandCompleter:
        return CommandCompleter(self)

    @cached_property
    def input(self) -> TextArea:
        return TextArea(
            height=1,
            prompt=PROMPT,
            multiline=False,
            wrap_lines=False,
            history=InMemoryHistory(),
            completer=self.completer,
            complete_while_typing=False,
            accept_handler=self._accept,
        )

    @cached_property
    def bindings(self) -> KeyBindings:
        bindings = KeyBindings()
        empty = Condition(lambda: not self.input.text)

        @bindings.add("c-c")
        @bindings.add("c-d", filter=empty)
        def _quit(event: KeyPressEvent) -> None:
            event.app.exit()

        @bindings.add(Keys.BracketedPaste)
        def _paste(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text(self.pasted(event.data))

        @bindings.add("c-l")
        def _clear(event: KeyPressEvent) -> None:
            self.clear()

        @bindings.add("c-o")
        def _mouse(event: KeyPressEvent) -> None:
            self.mouse = not self.mouse

        panes = Condition(lambda: self.panes)

        # without panes the scrollback belongs to the terminal, which has its own keys for it
        @bindings.add("pageup", filter=panes)
        def _up(event: KeyPressEvent) -> None:
            self.feed.scroll(-self.feed.page)

        @bindings.add("pagedown", filter=panes)
        def _down(event: KeyPressEvent) -> None:
            self.feed.scroll(self.feed.page)

        return bindings

    def body(self) -> Container:
        """
        The feed and the panes side by side. It is built once per change to the set of panes and
        held afterwards, because a window carries its own scroll position and a fresh one starts
        back at the tail.
        """
        if self._body is None:
            children: list[AnyContainer] = [self.feed.container]
            children.extend(
                column.container
                for column in self.columns
            )
            self._body = VSplit(
                children,
                padding=1,
                padding_char=VERTICAL,
                padding_style="class:separator",
            )
        return self._body

    def clear(self) -> None:
        """
        Empty the terminal, its scrollback included, leaving what is tracked alone. The rows each
        pane retains go with it, since those are what `copy` would otherwise hand back.
        """
        self.feed.clear()
        for column in self.columns:
            column.clear()
        renderer = self.application.renderer
        renderer.clear()
        # the screen is the renderer's to erase; the scrollback behind it is not, and that is
        # where everything the console printed has gone
        output = self.application.output
        output.write_raw("\x1b[3J")
        output.flush()

    def reflow(self) -> None:
        """Drops the built layout so the next render lays the panes out as they now stand."""
        self._body = None
        self.application.invalidate()

    @property
    def mouse(self) -> bool:
        """
        Whether the terminal reports the mouse to the panes. Reporting is what carries the wheel
        to the pane under the pointer; it is also what takes click-and-drag away from the
        terminal's own selection, so releasing it is how a reader copies out of a pane.
        """
        return self._mouse

    @mouse.setter
    def mouse(self, enabled: bool) -> None:
        self._mouse = enabled
        if enabled:
            self.feed.info("mouse: held; the wheel scrolls the pane under the pointer")
        else:
            self.feed.info(
                "mouse: released to the terminal; drag to select and copy, "
                "middle-click to paste, ctrl-o to take it back"
            )
        self.application.invalidate()

    @staticmethod
    def pasted(data: str) -> str:
        """
        A paste as the one line the command bar is. A line copied whole out of a terminal arrives
        padded with spaces out to the terminal's width, and often with the break that ended it;
        the bar is a single row that scrolls to follow the cursor, so the padding is all the row
        would have left to show, and the break would put the cursor on a second row it cannot
        display at all. Either way the bar looks empty while holding what was pasted.

        The ends of each line go and the spaces within one stay, so that a path holding a space
        survives being pasted.
        """
        lines = [
            line.strip()
            for line in data.splitlines()
        ]
        return " ".join(
            line
            for line in lines
            if line
        )

    @cached_property
    def layout(self) -> Layout:
        header = Window(
            FormattedTextControl(self.renderer.header),
            height=1,
            style="class:header",
        )
        if not self.panes:
            # only the header and the bar are drawn; everything above them is the terminal's
            container = HSplit([header, self.input])
        else:
            container = HSplit(
                [
                    header,
                    DynamicContainer(self.body),
                    Window(height=1, char="─", style="class:separator"),
                    self.input,
                ]
            )
        return Layout(container, focused_element=self.input)

    @cached_property
    def application(self) -> Application:
        return Application(
            layout=self.layout,
            key_bindings=self.bindings,
            style=STYLE,
            full_screen=self.panes,
            mouse_support=Condition(lambda: self.panes and self._mouse),
            # coalesce the redraws of a burst of filesystem events into one
            min_redraw_interval=self.interval,
        )

    async def run(self) -> None:
        trail = self._trail
        application = self.application
        self._banner()
        self._replay()
        # the cursor is taken here, before the observer is started, so that nothing recorded
        # between the replayed backlog and the first wakeup falls between them
        watching = trail.events.watch()
        async with trail.watchdog.context():
            tasks = [
                asyncio.create_task(self._stream(watching), name="trail-console-stream"),
                asyncio.create_task(self._monitor(), name="trail-console-monitor"),
            ]
            try:
                with patch_stdout(raw=True):
                    await application.run_async()
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with suppress(asyncio.CancelledError):
                        await task
                await watching.aclose()

    async def _stream(self, watching: AsyncIterator[Event]) -> None:
        """
        The watch supplies the wakeup and the cursor supplies the batching, so a burst of
        filesystem events is rendered in a single pass however many wakeups it delivers.
        """
        async for _event in watching:
            if self._drain():
                self.application.invalidate()

    async def _monitor(self) -> None:
        """A watch only wakes on an event, so the observer's own health is checked apart."""
        watchdog = self._trail.watchdog
        while True:
            await asyncio.sleep(self.heartbeat)
            self._diagnose(watchdog.consumer)

    def _diagnose(self, consumer: asyncio.Task[None] | None) -> None:
        """A consumer that dies takes the live feed with it, so say so rather than fall silent."""
        if (
            self.failed
            or consumer is None
            or not consumer.done()
            or consumer.cancelled()
        ):
            return
        self.failed = True
        failure = consumer.exception()
        if failure is not None:
            self.feed.error(f"watchdog stopped: {failure!r}")
            self.application.invalidate()

    def _drain(self) -> bool:
        """Writes the events appended to the log since the last sweep to every open feed."""
        events = self._trail.events
        identifiers = events.ids
        # a cleared log leaves the cursor beyond it, and everything appended afterwards would
        # go unrendered until the log grew back past where it had been
        self.cursor = min(self.cursor, len(identifiers))
        if len(identifiers) <= self.cursor:
            return False
        start = self.cursor
        pending = [
            events[identifier]
            for identifier in identifiers[start:]
        ]
        self.cursor = len(identifiers)
        self.feed.write(pending, start)
        if self.panes:
            for column in self.columns:
                column.write(pending, start)
        return True

    def _banner(self) -> None:
        trail = self._trail
        renderer = self.renderer
        self.feed.info(f"trail #{trail.id[:8]} on {renderer.home(self.root)}")
        if trail.dir is None:
            self.feed.info("nodir: events are held in memory and discarded on exit")
        else:
            self.feed.info(f"recording to {renderer.home(trail.dir)}")
        self.feed.info("nothing is tracked until you register it; 'help' lists the commands")

    def _replay(self) -> None:
        """Renders the tail of a previous session's log before the live feed takes over."""
        identifiers = self._trail.events.ids
        hidden = len(identifiers) - self.backlog
        if hidden > 0:
            self.feed.info(f"... {hidden} earlier events")
            self.cursor = hidden
        self._drain()

    def _accept(self, buffer: Buffer) -> bool:
        self.submit(buffer.text)
        return False

    def submit(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        self.feed.echo(text)
        try:
            tokens = shlex.split(text)
        except ValueError as error:
            self.feed.error(f"unbalanced quotes: {error}")
            return
        if not tokens:
            return
        command = self.commands.resolve(tokens[0])
        if command is not None:
            # a REPL outlives a mistyped argument; anything raised belongs in the feed
            try:
                command(tokens[1:])
            except Exception as error:  # noqa: BLE001
                self.feed.error(f"{type(error).__name__}: {error}")
        self._drain()
