from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from trail.cli.console import Console
from trail.trail import Trail


class TestListing:
    """
    The commands that select out of a collection. The feed prints its rows to the terminal;
    the printing is swallowed and the rows are read back off the feed instead.
    """

    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "folder").mkdir()
            for name in ("a.csv", "b.csv", "c.csv"):
                (root / name).write_text(f"{name}\n", encoding="utf-8")
            yield root

    @classmethod
    @contextmanager
    def session(cls) -> Iterator[Console]:
        with ExitStack() as stack:
            root = stack.enter_context(cls.workspace())
            pipe = stack.enter_context(create_pipe_input())
            stack.enter_context(create_app_session(input=pipe, output=DummyOutput()))
            console = Console(Trail(root), root)
            cls.run(console, "track a.csv b.csv c.csv folder")
            yield console

    @staticmethod
    def run(
        console: Console,
        line: str,
    ) -> str:
        """Submit a line and return only what it wrote, with the printing swallowed."""
        before = len(console.feed.rows)
        with redirect_stdout(io.StringIO()):
            console.submit(line)
        return "\n".join(
            "".join(fragment[1] for fragment in row)
            for row in console.feed.rows[before:]
        )

    @staticmethod
    def positions(written: str) -> list[int]:
        return [
            int(line.split(".", 1)[0])
            for line in written.splitlines()
            if line and line[0].isdigit() and ". " in line
        ]

    def test_a_slice_counts_the_way_python_counts(self) -> None:
        with self.session() as console:
            assert self.positions(self.run(console, "events :2")) == [0, 1]
            assert self.positions(self.run(console, "events -2:")) == [2, 3]
            assert self.positions(self.run(console, "events 2:")) == [2, 3]
            assert self.positions(self.run(console, "events 1:3")) == [1, 2]

    def test_a_filter_keeps_the_position_the_collection_addresses(self) -> None:
        with self.session() as console:
            entry = console._trail.assets[console.root / "c.csv"]
            written = self.run(console, f"events entry={entry.id}")
            # c.csv was tracked third, and stays the third record however few are shown
            assert self.positions(written) == [2]
            assert entry.id in written

    def test_a_path_filter_takes_either_way_of_writing_it(self) -> None:
        with self.session() as console:
            absolute = self.run(console, f"events src_path={console.root / 'b.csv'}")
            relative = self.run(console, "events src_path=b.csv")
            assert self.positions(absolute) == self.positions(relative) == [1]

    def test_a_bare_value_tries_the_fields_in_turn(self) -> None:
        with self.session() as console:
            trail = console._trail
            event = trail.events.by_pos[1]
            entry = trail.assets[console.root / "c.csv"]
            # the record's own id, then the resource's, then a path, each found without being named
            assert self.positions(self.run(console, f"events {event.id}")) == [1]
            assert self.positions(self.run(console, f"events {entry.id}")) == [2]
            assert self.positions(self.run(console, f"events {console.root / 'a.csv'}")) == [0]
            # an id is only ever shown shortened, so a prefix has to answer too
            assert self.positions(self.run(console, f"events {entry.id[:8]}")) == [2]

    def test_assets_and_dirs_list_their_own_collections(self) -> None:
        with self.session() as console:
            assert self.positions(self.run(console, "assets :2")) == [0, 1]
            assert self.positions(self.run(console, "assets 1:")) == [1, 2]
            written = self.run(console, f"assets path={console.root / 'b.csv'}")
            assert self.positions(written) == [1]
            assert "Asset" in written
            assert "Dir" in self.run(console, "dirs")
            # a directory is not an asset, so the one collection does not answer for the other
            assert "no record holds" in self.run(console, f"assets {console.root / 'folder'}")
            assert self.positions(self.run(console, f"dirs {console.root / 'folder'}")) == [0]

    def test_values_for_the_one_field_read_as_alternatives(self) -> None:
        with self.session() as console:
            assert self.positions(self.run(console, "assets a.csv b.csv")) == [0, 1]
            # named outright, the one field twice over reads the same way
            written = self.run(console, "assets path=a.csv path=b.csv")
            assert self.positions(written) == [0, 1]

    def test_values_for_different_fields_all_have_to_hold(self) -> None:
        with self.session() as console:
            assert self.positions(self.run(console, "assets path=a.csv name=a.csv")) == [0]
            # the one record cannot hold both, so pairing them keeps nothing
            assert "nothing matched" in self.run(console, "assets path=a.csv name=b.csv")

    def test_a_slice_cuts_what_the_values_left(self) -> None:
        with self.session() as console:
            # three records name a.csv, once it has been let go of and taken back
            self.run(console, "untrack a.csv")
            self.run(console, "track a.csv")
            assert self.positions(self.run(console, "events a.csv")) == [0, 4, 5]
            assert self.positions(self.run(console, "events a.csv -2:")) == [4, 5]
            assert self.positions(self.run(console, "events a.csv 1")) == [5]
            # the position stays the one the collection addresses, not the one among the results
            assert self.positions(self.run(console, "events a.csv b.csv -3:")) == [1, 4, 5]

    def test_slices_cut_in_the_order_they_were_written(self) -> None:
        with self.session() as console:
            assert self.positions(self.run(console, "events :3 1:")) == [1, 2]
            assert self.positions(self.run(console, "events 1: :1")) == [1]

    def test_a_field_that_does_not_exist_is_named_back(self) -> None:
        with self.session() as console:
            written = self.run(console, "events bogus=1")
            assert "no field 'bogus'" in written
            assert "src_path" in written
            assert "nothing matched" in self.run(console, "events event_type=created")
            assert "no record holds" in self.run(console, "events nowhere")

    def test_the_key_empties_the_rows_but_not_the_tracking(self) -> None:
        """Emptying the terminal is ctrl-l's, now that `clear` discards the record instead."""
        with self.session() as console:
            tracked = len(console._trail.entries)
            assert console.feed.rows
            with redirect_stdout(io.StringIO()):
                console.clear()
            assert not console.feed.rows
            assert len(console._trail.entries) == tracked
            assert self.positions(self.run(console, "events :1")) == [0]

    def test_a_clear_says_what_would_go_before_it_goes(self) -> None:
        with self.session() as console:
            trail = console._trail
            written = self.run(console, "assets clear")
            assert "assets: this clears 3" in written
            assert "'assets clear -f' to confirm" in written
            assert len(trail.assets) == 3
            # an argument that is not the confirmation is not taken for one
            assert "usage: assets clear" in self.run(console, "assets clear now")
            assert len(trail.assets) == 3

    def test_clearing_a_collection_unregisters_and_stays_cleared(self) -> None:
        with self.session() as console:
            trail = console._trail
            root = console.root
            assert "assets: cleared 3" in self.run(console, "assets clear -f")
            assert not len(trail.assets)
            # a directory is a collection of its own, and keeps what it holds
            assert len(trail.dirs) == 1
            # the removals were recorded, so the log replays into the same emptiness
            reopened = Trail(root)
            assert not len(reopened.assets)
            assert len(reopened.dirs) == 1

    def test_clearing_the_entries_takes_both_collections(self) -> None:
        with self.session() as console:
            trail = console._trail
            recorded = len(trail.events)
            assert "entries: cleared 4" in self.run(console, "entries clear -f")
            assert not len(trail.assets)
            assert not len(trail.dirs)
            # the log is untouched, and now carries the removals as well
            assert len(trail.events) == recorded + 4

    def test_clearing_the_events_empties_the_log(self) -> None:
        with self.session() as console:
            trail = console._trail
            root = console.root
            recorded = len(trail.events)
            assert f"events: this clears {recorded}" in self.run(console, "events clear")
            assert f"events: cleared {recorded}" in self.run(console, "events clear -f")
            assert not len(trail.events)
            # a session is rebuilt from the log, so nothing it held comes back with it
            assert not len(Trail(root).entries)
            assert "already empty" in self.run(console, "events clear")

    def test_the_feed_renders_what_is_recorded_after_a_clear(self) -> None:
        with self.session() as console:
            trail = console._trail
            self.run(console, "events clear -f")
            (console.root / "d.csv").write_text("d\n", encoding="utf-8")
            # the feed's cursor was left beyond a log that had shrunk under it
            written = self.run(console, "track d.csv")
            assert "AddEntryEvent" in written, written
            assert len(trail.events) == 1

    def test_clear_discards_the_whole_record(self) -> None:
        with self.session() as console:
            trail = console._trail
            entries = len(trail.entries)
            events = len(trail.events)
            written = self.run(console, "clear")
            assert f"clear: this clears entries {entries}, events {events}" in written
            assert len(trail.entries) == entries
            assert len(trail.events) == events
            confirmed = self.run(console, "clear -f")
            assert f"clear: cleared entries {entries}, events {events}" in confirmed
            assert not len(trail.entries)
            assert not len(trail.events)
            assert "already empty" in self.run(console, "clear")
