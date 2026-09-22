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
    FormattedTextControl,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.widgets import TextArea

from trail.cli.command import CommandCompleter
from trail.cli.commands import Commands
from trail.cli.feed import Feed
from trail.cli.node import Node
from trail.cli.render import Renderer
from trail.cli.theme import PROMPT, STYLE
from trail.event import Event
from trail.trail import Trail


class Console(Node):
    """
    The interactive session. Whatever the watchdog records streams into the feed as it happens,
    and the command bar below edits what is tracked, which is what produces those records.

    Only the header and the bar are drawn. Everything above them is ordinary terminal output,
    so scrollback, selection and the scrollbar keep working as they always do; the alternate
    screen would have taken all three away.
    """

    _parent: Trail
    # seconds between checks that the observer is still alive; its death announces itself
    # in no event, so it has to be looked for
    heartbeat = 1.0
    # redraws arriving within this interval are coalesced into one
    interval = 0.05
    # events from previous sessions replayed on startup
    backlog = 20

    def __init__(
        self,
        trail: Trail,
        root: Path,
    ) -> None:
        Node.__init__(self, trail)
        self.root = root
        self.cursor = 0
        self.failed = False
        self.restarting = False

    @cached_property
    def feed(self) -> Feed:
        return Feed(self)

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

        return bindings

    def clear(self) -> None:
        """
        Empty the terminal and its scrollback. Nothing tracked is touched. The feed drops its
        rows too, so what it holds still matches what is on the screen.
        """
        self.feed.clear()
        renderer = self.application.renderer
        renderer.clear()
        # the renderer erases the screen but not the scrollback behind it, and the scrollback
        # is where everything the console printed has gone
        output = self.application.output
        output.write_raw("\x1b[3J")
        output.flush()

    def restart(self) -> None:
        """
        Exit the session with a note to come back.

        A running session cannot pick up an edit to its own source: the classes were read at
        import, and the registry and the caches are built from those. Only a new interpreter
        can. `main` performs the exec rather than this method, because it has to wait until the
        application has restored the terminal and the watchdog has stopped, which is what
        returning from `run` means.
        """
        self.restarting = True
        self.feed.info("restart: reopening on the same command line")
        self.application.exit()

    @staticmethod
    def pasted(data: str) -> str:
        """
        Flatten a paste onto the single line the command bar is.

        A line copied out of a terminal usually arrives padded with spaces to the terminal's
        width, often with the newline that ended it. The bar is one row and scrolls to follow
        the cursor, so it would show only the padding; a newline would move the cursor to a
        second row it cannot draw at all. Either way the bar looks empty while holding a paste.

        Only the ends of each line are stripped, never the spaces inside one, so a path with a
        space in it survives.
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
        # everything above these two is the terminal's, printed and never redrawn
        container = HSplit([header, self.input])
        return Layout(container, focused_element=self.input)

    @cached_property
    def application(self) -> Application:
        return Application(
            layout=self.layout,
            key_bindings=self.bindings,
            style=STYLE,
            # coalesce the redraws of a burst of filesystem events into one
            min_redraw_interval=self.interval,
        )

    async def run(self) -> None:
        trail = self._trail
        application = self.application
        self._banner()
        self._replay()
        # taken before the observer starts, so that nothing recorded between the replayed
        # backlog and the first wakeup slips through the gap
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
        The watch says when to look and the cursor says what is new, so a burst of filesystem
        events is rendered in one pass, no matter how many wakeups it arrives in.
        """
        async for _event in watching:
            if self._drain():
                self.application.invalidate()

    async def _monitor(self) -> None:
        """The watch only wakes on an event, so a dead observer would never announce itself."""
        watchdog = self._trail.watchdog
        while True:
            await asyncio.sleep(self.heartbeat)
            self._diagnose(watchdog.consumer)

    def _diagnose(self, consumer: asyncio.Task[None] | None) -> None:
        """A dead consumer takes the live feed with it, so say so rather than fall silent."""
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
        """Write the events appended to the log since the last sweep."""
        events = self._trail.events
        identifiers = events.ids
        # a cleared log leaves the cursor past the end, and anything appended afterwards would
        # go unrendered until the log grew back to where the cursor was
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
        return True

    def _banner(self) -> None:
        trail = self._trail
        renderer = self.renderer
        self.feed.info(f"trail #{trail.id} on {renderer.home(self.root)}")
        if trail.dir is None:
            self.feed.info("nodir: events are held in memory and discarded on exit")
        else:
            self.feed.info(f"recording to {renderer.home(trail.dir)}")
        self.feed.info("nothing is tracked until you track it; 'help' lists the commands")

    def _replay(self) -> None:
        """Render the tail of a previous session's log before the live feed takes over."""
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
