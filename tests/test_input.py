from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from trail.cli.console import Console
from trail.trail import Trail

PATH = "/dev/shm/tmpf6v_044j/csv.csv"
# what the terminal filled the rest of the line with, and a double-click took along
PADDING = " " * 52


class TestPaste:
    """
    What the command bar makes of a paste. A line copied whole out of a terminal carries the
    padding that filled the terminal's width, and often the break that ended it; the bar is a
    single row that scrolls to follow the cursor, so either one leaves it looking empty while it
    holds what was pasted.
    """

    def test_a_padded_line_loses_only_its_padding(self) -> None:
        assert Console.pasted(f"{PATH}{PADDING}") == PATH
        assert Console.pasted(f"  {PATH}  ") == PATH
        assert Console.pasted(PATH) == PATH

    def test_a_line_break_does_not_reach_a_bar_that_is_one_line(self) -> None:
        assert Console.pasted(f"{PATH}\n") == PATH
        assert Console.pasted(f"{PATH}\r\n") == PATH
        assert Console.pasted(f"  {PATH}  \r\nother.csv\r\n") == f"{PATH} other.csv"
        assert Console.pasted("\n\n") == ""

    def test_a_path_holding_a_space_survives_the_paste(self) -> None:
        """Only the ends of a line go, since the space between two words of a name is the name."""
        assert Console.pasted("/tmp/two  spaces.csv   ") == "/tmp/two  spaces.csv"

    def test_the_bar_holds_what_a_double_click_pasted(self) -> None:
        """The binding has to win over the one prompt_toolkit installs for a paste of its own."""

        async def run() -> None:
            with ExitStack() as stack:
                root = Path(stack.enter_context(TemporaryDirectory())).resolve()
                pipe = stack.enter_context(create_pipe_input())
                stack.enter_context(create_app_session(input=pipe, output=DummyOutput()))
                console = Console(Trail(root), root)
                pipe.send_text(f"\x1b[200~{PATH}{PADDING}\r\n\x1b[201~")

                async def look() -> None:
                    try:
                        await asyncio.sleep(0.3)
                        document = console.input.buffer.document
                        # the cursor is at the end of the path, not out in the padding the row
                        # would have had to scroll to, and on the only row the bar can show
                        assert document.text == PATH
                        assert document.line_count == 1
                        assert document.cursor_position_col == len(PATH)
                    finally:
                        console.application.exit()

                looking = asyncio.create_task(look())
                async with asyncio.timeout(20):
                    await console.application.run_async()
                    await looking

        asyncio.run(run())
