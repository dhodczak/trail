from __future__ import annotations

from collections.abc import Iterator
from contextlib import chdir, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.event import Events


class TestSelect:
    """
    Five events: a.csv, b.csv and c.csv tracked, a.csv offtrailed, then tracked again under a
    new entry. Their timestamps are pinned a minute apart from 10:00 UTC today.
    """

    @staticmethod
    @contextmanager
    def session() -> Iterator[tuple[Trail, Path]]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name, size in (("a.csv", 1), ("b.csv", 10), ("c.csv", 100)):
                (root / name).write_text("x" * size, encoding="utf-8")
            trail = Trail(root)
            trail.track(root / "a.csv")
            trail.track(root / "b.csv")
            trail.track(root / "c.csv")
            trail.offtrail(root / "a.csv")
            trail.track(root / "a.csv")
            start = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)
            for position, event in enumerate(trail.events.values()):
                event.timestamp = start + timedelta(minutes=position)
            yield trail, root

    @staticmethod
    def positions(
        trail: Trail,
        selection: Events,
    ) -> list[int]:
        return [
            trail.events.ids.index(identifier)
            for identifier in selection.ids
        ]

    def test_a_slice_counts_the_way_python_counts(self) -> None:
        with self.session() as (trail, _):
            assert self.positions(trail, trail.events.select(":2")) == [0, 1]
            assert self.positions(trail, trail.events.select("-2:")) == [3, 4]
            assert self.positions(trail, trail.events.select("1::2")) == [1, 3]
            assert self.positions(trail, trail.events.select[:]) == [0, 1, 2, 3, 4]
            assert self.positions(trail, trail.events.select("")) == [0, 1, 2, 3, 4]

    def test_a_selection_is_a_copy_sharing_the_parent(self) -> None:
        with self.session() as (trail, _):
            events = trail.events
            selection = events.select("-2:")
            assert type(selection) is Events
            assert selection is not events
            assert selection._parent is trail
            assert selection.ids == list(selection.data)
            assert selection.select[0] is events.select[3]
            assert len(events) == 5
            # the copy's own select is bound to it, so a selection can be selected from
            assert self.positions(trail, selection.select(":1")) == [3]

    def test_every_term_names_its_field(self) -> None:
        with self.session() as (trail, root):
            events = trail.events
            event = events.select[1]
            first = events.select[0].entry
            assert self.positions(trail, events.select(f"id={event.id}")) == [1]
            # the entry is held by reference, and compares by its id
            assert self.positions(trail, events.select(f"entry={first.id}")) == [0, 3]
            assert self.positions(trail, events.select(f"src_path={root / 'a.csv'}")) == [0, 3, 4]
            # an id matches whole, not by prefix
            assert not events.select(f"id={event.id[:8]}")
            with pytest.raises(ValueError, match="not a comparison"):
                events.select(str(root / "a.csv"))

    def test_quotes_and_lines_split_the_way_a_shell_splits_them(self) -> None:
        with self.session() as (trail, root):
            spaced = root / "d (e).csv"
            spaced.write_text("x", encoding="utf-8")
            trail.track(spaced)
            events = trail.events
            assert self.positions(trail, events.select(f"src_path='{spaced}'")) == [5]
            assert self.positions(trail, events.select(f'"src_path={spaced}"')) == [5]
            multiline = f"""
                src_path={root / "b.csv"}
                or src_path={root / "c.csv"}
            """
            assert self.positions(trail, events.select(multiline)) == [1, 2]

    def test_or_pools_what_either_side_selects(self) -> None:
        with self.session() as (trail, root):
            events = trail.events
            a = root / "a.csv"
            b = root / "b.csv"
            either = events.select(f"src_path={a} or src_path={b}")
            assert self.positions(trail, either) == [0, 1, 3, 4]
            first = events.select[0].id
            last = events.select[4].id
            assert self.positions(trail, events.select(f"id={first} or id={last}")) == [0, 4]

    def test_not_and_parentheses(self) -> None:
        with self.session() as (trail, root):
            events = trail.events
            a = root / "a.csv"
            b = root / "b.csv"
            last = events.select[4].id
            assert self.positions(trail, events.select(f"not src_path={a}")) == [1, 2]
            neither = events.select(f"not (src_path={a} or src_path={b})")
            assert self.positions(trail, neither) == [2]
            written = events.select(f"src_path={a} and not id={last}")
            assert self.positions(trail, written) == [0, 3]
            assert self.positions(trail, events.select("((-1:))")) == [4]

    def test_and_binds_tighter_than_or(self) -> None:
        with self.session() as (trail, root):
            events = trail.events
            a = root / "a.csv"
            b = root / "b.csv"
            c = root / "c.csv"
            ungrouped = events.select(f"src_path={b} or src_path={a} -1:")
            assert self.positions(trail, ungrouped) == [1, 4]
            grouped = events.select(f"(src_path={b} or src_path={a}) -1:")
            assert self.positions(trail, grouped) == [4]
            juxtaposed = events.select(f"src_path={b} or src_path={c} src_path={a}")
            assert self.positions(trail, juxtaposed) == [1]

    def test_terms_side_by_side_chain_in_the_order_written(self) -> None:
        with self.session() as (trail, root):
            events = trail.events
            a = root / "a.csv"
            b = root / "b.csv"
            both = f"(src_path={a} or src_path={b})"
            assert self.positions(trail, events.select(f"{both} -2:")) == [3, 4]
            assert self.positions(trail, events.select(f"-4: src_path={b}")) == [1]
            assert not events.select(f"-3: src_path={b}")
            assert self.positions(trail, events.select("not -2:")) == [0, 1, 2]
            identifier = events.select[4].id
            assert self.positions(trail, events.select(f"id={identifier} src_path={a}")) == [4]
            assert not events.select(f"id={identifier} and src_path={b}")

    def test_a_malformed_expression_says_where(self) -> None:
        with self.session() as (trail, root):
            events = trail.events
            a = root / "a.csv"
            with pytest.raises(ValueError, match="unclosed"):
                events.select(f"(src_path={a}")
            with pytest.raises(ValueError, match="unmatched"):
                events.select(f"src_path={a})")
            with pytest.raises(ValueError, match="ends where a term should be"):
                events.select(f"src_path={a} or")
            with pytest.raises(ValueError, match="expected a term"):
                events.select(f"or src_path={a}")
            with pytest.raises(ValueError, match="not a comparison"):
                events.select("nowhere")

    def test_comparisons_coerce_to_what_the_field_holds(self) -> None:
        with self.session() as (trail, root):
            events = trail.events
            # the removal holds no size, so no ordering holds for it
            assert self.positions(trail, events.select("st_size>=10")) == [1, 2]
            assert self.positions(trail, events.select("st_size<10")) == [0, 4]
            assert self.positions(trail, events.select("is_directory=false")) == [0, 1, 2, 4]
            assert self.positions(trail, events.select(f"src_path!={root / 'a.csv'}")) == [1, 2]
            # `!=` negates `=`, so an event without the field is kept
            assert self.positions(trail, events.select("event_type!=opened")) == [0, 1, 2, 3, 4]
            with pytest.raises(ValueError):
                events.select("st_size<big")

    def test_cls_holds_for_the_class_and_its_bases(self) -> None:
        with self.session() as (trail, _):
            events = trail.events
            assert self.positions(trail, events.select("cls=AddEntryEvent")) == [0, 1, 2, 4]
            assert self.positions(trail, events.select("cls=RemoveEntryEvent")) == [3]
            assert self.positions(trail, events.select("cls!=AddEntryEvent")) == [3]
            # as isinstance would, rather than the exact class
            assert self.positions(trail, events.select("cls=Event")) == [0, 1, 2, 3, 4]
            assert not trail.assets.select("cls=Dir")
            assert len(trail.entries.select("cls=Entry")) == 3
            with pytest.raises(TypeError, match="only by = and !="):
                events.select("cls<Event")

    def test_a_timestamp_reads_as_iso_or_as_the_repr_prints_it(self) -> None:
        with self.session() as (trail, _):
            events = trail.events
            moment = events.select[2].timestamp.isoformat()
            assert self.positions(trail, events.select(f"timestamp>={moment}")) == [2, 3, 4]
            # a time alone is today's, in UTC, which is how the repr prints it
            assert self.positions(trail, events.select("timestamp<10:02")) == [0, 1]
            assert self.positions(trail, events.select("timestamp=10:03:00")) == [3]

    def test_changing_a_selection_leaves_the_log_alone(self) -> None:
        with self.session() as (trail, _):
            events = trail.events
            history = events.jsonl.path.read_bytes()
            selection = events.select("-2:")
            assert selection.jsonl.path is None
            del selection[selection.ids[0]]
            selection.clear()
            assert events.jsonl.path.read_bytes() == history
            assert len(events) == 5

    def test_entries_select_by_id_path_and_name(self) -> None:
        with self.session() as (trail, root):
            assets = trail.assets
            b = assets[root / "b.csv"]
            c = assets[root / "c.csv"]
            selection = assets.select(f"id={b.id}")
            assert type(selection) is type(assets)
            assert list(selection.values()) == [b]
            assert list(assets.select(f"path={root / 'b.csv'}").values()) == [b]
            # an entry's path is a Path, so a relative one is resolved before it is compared
            with chdir(root):
                assert list(assets.select("path=b.csv").values()) == [b]
            assert list(assets.select("name=c.csv or name=b.csv").values()) == [b, c]
            assert assets.select[0] is b
            assert list(assets.select[-1:].values()) == [assets[root / "a.csv"]]

    def test_changing_an_entries_selection_leaves_the_collection_alone(self) -> None:
        with self.session() as (trail, root):
            assets = trail.assets
            b = root / "b.csv"
            selection = assets.select[:]
            assert selection.path2entry is not assets.path2entry
            del selection[b]
            assert b not in selection
            assert b in assets
            assert b in trail.entries
            assert assets[b].path == b


if __name__ == "__main__":
    test_object = TestSelect()
    tests = [
        (
            "test_a_slice_counts_the_way_python_counts",
            "a slice counts the way python counts",
        ),
        (
            "test_a_selection_is_a_copy_sharing_the_parent",
            "a selection is a copy sharing the parent",
        ),
        (
            "test_every_term_names_its_field",
            "every term names its field",
        ),
        (
            "test_quotes_and_lines_split_the_way_a_shell_splits_them",
            "quotes and lines split the way a shell splits them",
        ),
        (
            "test_or_pools_what_either_side_selects",
            "or pools what either side selects",
        ),
        (
            "test_not_and_parentheses",
            "not and parentheses",
        ),
        (
            "test_and_binds_tighter_than_or",
            "and binds tighter than or",
        ),
        (
            "test_terms_side_by_side_chain_in_the_order_written",
            "terms side by side chain in the order written",
        ),
        (
            "test_a_malformed_expression_says_where",
            "a malformed expression says where",
        ),
        (
            "test_comparisons_coerce_to_what_the_field_holds",
            "comparisons coerce to what the field holds",
        ),
        (
            "test_cls_holds_for_the_class_and_its_bases",
            "cls holds for the class and its bases",
        ),
        (
            "test_a_timestamp_reads_as_iso_or_as_the_repr_prints_it",
            "a timestamp reads as iso or as the repr prints it",
        ),
        (
            "test_changing_a_selection_leaves_the_log_alone",
            "changing a selection leaves the log alone",
        ),
        (
            "test_entries_select_by_id_path_and_name",
            "entries select by id, path and name",
        ),
        (
            "test_changing_an_entries_selection_leaves_the_collection_alone",
            "changing an entries selection leaves the collection alone",
        ),
    ]

    for method_name, description in tests:
        getattr(test_object, method_name)()
        print(f"  ✓ {description}")
