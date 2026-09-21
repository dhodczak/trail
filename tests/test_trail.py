from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from trail import Trail
from trail.checkpoint import Checkpoint
from trail.event import AddEntryEvent, RemoveEntryEvent, WatchdogEvent


class TestTrail:
    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            csv = root / "dataset.csv"
            csv.write_text("name,value\nexample,42\n", encoding="utf-8")
            yield root

    @staticmethod
    def opened_event(
        trail: Trail,
        csv: Path,
        previous: int,
    ) -> WatchdogEvent | None:
        return next(
            (
                event
                for event in trail.events.by_pos[previous:]
                if isinstance(event, WatchdogEvent)
                and event.event_type == "opened"
                and event.src_path == str(csv)
            ),
            None,
        )

    def test_pathless_track_and_open(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / "dataset.csv"
                trail = Trail()
                entry = trail.track(csv)
                addition = trail.events.by_pos[0]
                assert isinstance(addition, AddEntryEvent)
                assert addition.entry is entry
                assert trail.watchdog.observer.is_alive()
                previous = len(trail.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while (
                        opened := self.opened_event(trail, csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()
                assert opened.entry is entry
                assert trail.dir is None
                assert trail.events.jsonl.path is None
                assert list(root.iterdir()) == [csv]

        asyncio.run(run())

    def test_passive_instantiation_observes_without_a_context(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / "dataset.csv"
                trail = Trail()
                entry = trail.track(csv)
                assert trail.watchdog.running
                assert trail.watchdog.observer.is_alive()
                previous = len(trail.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while (opened := self.opened_event(trail, csv, previous)) is None:
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()
                assert opened.entry is entry
                assert not trail.watchdog.running

        asyncio.run(run())

    def test_passive_instantiation_is_dormant_without_a_loop(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            trail = Trail()
            entry = trail.track(csv)
            assert not trail.watchdog.ensure()
            assert not trail.watchdog.running
            assert trail.watchdog.dir2ids[root] == {entry.id}

            async def run() -> None:
                # the dormant membership becomes a live watch once a loop exists
                assert trail.watchdog.ensure()
                assert trail.watchdog.observer.is_alive()
                previous = len(trail.events)
                with csv.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nexample,42\n'
                async with asyncio.timeout(5):
                    while (
                        opened := self.opened_event(trail, csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()
                assert opened.entry is entry

            asyncio.run(run())

    def test_watchdog_context_starts_and_stops_the_session(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            trail = Trail()
            entry = trail.track(csv)
            assert not trail.watchdog.running

            async def run() -> None:
                async with trail.watchdog.context():
                    assert trail.watchdog.running
                    observer = trail.watchdog.observer
                    assert observer.is_alive()
                    assert root in trail.watchdog.watches
                    previous = len(trail.events)
                    with csv.open(encoding='utf-8') as stream:
                        assert stream.read() == 'name,value\nexample,42\n'
                    async with asyncio.timeout(5):
                        while (
                            opened := self.opened_event(trail, csv, previous)
                        ) is None:
                            await asyncio.sleep(0.01)

                assert opened.entry is entry
                # the session is torn down, but the directory membership outlives it
                assert not trail.watchdog.running
                assert not observer.is_alive()
                assert trail.watchdog.observer is None
                assert trail.watchdog.queue is None
                assert trail.watchdog.watches is None
                assert trail.watchdog.dir2ids[root] == {entry.id}

                # native observers cannot restart, so a second context builds a new one
                async with trail.watchdog.context():
                    assert trail.watchdog.running
                    assert trail.watchdog.observer is not observer
                    assert trail.watchdog.observer.is_alive()
                    assert root in trail.watchdog.watches
                assert not trail.watchdog.running

            asyncio.run(run())

    def test_watchdog_context_adopts_a_passive_session(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / 'dataset.csv'
                trail = Trail()
                trail.track(csv)
                assert trail.watchdog.running
                observer = trail.watchdog.observer

                # entering the context must adopt the running session, not build a second one
                async with trail.watchdog.context():
                    assert trail.watchdog.observer is observer
                    assert trail.watchdog.running

                assert not trail.watchdog.running
                assert not observer.is_alive()

        asyncio.run(run())

    def test_persistent_track_open_and_new_session(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / "dataset.csv"
                trail = Trail(root)
                entry = trail.track(csv)
                previous = len(trail.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while (
                        opened := self.opened_event(trail, csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                # quiet this trail so the reloaded ones are the only writers
                await trail.watchdog.stop()
                assert opened.entry is entry
                records = [
                    event.to_record()
                    for event in trail.events.by_pos[:]
                ]
                stored = [
                    json.loads(line)
                    for line in trail.events.jsonl.path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                ]
                assert stored == records
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    "import json, sys; from trail import Trail; "
                    "trail = Trail(sys.argv[1]); "
                    'print(json.dumps({"id": trail.id, '
                    '"events": [event.to_record() for event in trail.events.by_pos[:]], '
                    '"entry_id": trail.entries[sys.argv[2]].id}))',
                    str(root),
                    str(csv),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=5,
                )
                assert process.returncode == 0, stderr.decode()
                snapshot = json.loads(stdout)
                assert snapshot["id"] == trail.id
                assert snapshot["events"] == records
                assert snapshot["entry_id"] == entry.id
                restored = Trail(root)
                assert restored.id == trail.id
                assert [
                    event.to_record()
                    for event in restored.events.by_pos[:]
                ] == records
                restored_entry = restored.entries[csv]
                assert restored_entry.id == entry.id
                assert all(
                    event.entry is restored_entry
                    for event in restored.events.by_pos[:]
                )
                previous = len(restored.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while (
                        reopened := self.opened_event(restored, csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                await restored.watchdog.stop()
                assert reopened.entry is restored_entry
                previous_ids = {
                    record["id"]
                    for record in records
                }
                assert reopened.id not in previous_ids
                third = Trail(root)
                assert reopened.id in third.events
                await third.watchdog.stop()

        asyncio.run(run())

    def test_untracked_file_stays_untracked_after_new_session(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / 'dataset.csv'
                other_csv = root / 'other.csv'
                other_csv.write_text('name,value\nother,7\n', encoding='utf-8')
                trail = Trail(root)
                entry = trail.track(csv)
                other_entry = trail.track(other_csv)
                trail.untrack(csv)
                await trail.watchdog.stop()
                restored = Trail(root)
                assert csv not in restored.entries
                removal = restored.events.by_pos[-1]
                assert isinstance(removal, RemoveEntryEvent)
                assert removal.entry.id == entry.id
                previous = len(restored.events)

                with csv.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nexample,42\n'
                with other_csv.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nother,7\n'
                async with asyncio.timeout(5):
                    while (
                        opened := self.opened_event(restored, other_csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                await restored.watchdog.stop()

                assert opened.entry.id == other_entry.id
                assert csv not in restored.entries
                assert restored.events.by_pos[previous:] == [opened]
                reloaded = Trail(root)
                assert csv not in reloaded.entries
                await reloaded.watchdog.stop()

        asyncio.run(run())

    def test_track_untrack_and_retrack_history_can_be_restored(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            trail = Trail(root)
            original_entry = trail.track(csv)
            trail.untrack(csv)
            current_entry = trail.track(csv)
            assert current_entry.id != original_entry.id
            history = trail.events.jsonl.path.read_bytes()
            csv.unlink()

            restored = Trail(root)
            addition = restored.events.by_pos[0]
            removal = restored.events.by_pos[1]
            readdition = restored.events.by_pos[2]
            assert isinstance(addition, AddEntryEvent)
            assert isinstance(removal, RemoveEntryEvent)
            assert isinstance(readdition, AddEntryEvent)
            assert addition.entry is removal.entry
            assert addition.entry.id == original_entry.id
            assert readdition.entry is restored.entries[csv]
            assert readdition.entry.id == current_entry.id
            assert restored.events.jsonl.path.read_bytes() == history

    def test_deleted_csv_history_can_be_restored(self) -> None:
        async def run() -> None:
            # open a workspace
            with self.workspace() as root:
                # track the dataset.csv inside th workspace
                csv = root / "dataset.csv"
                trail = Trail(root)
                entry = trail.track(csv)
                previous = len(trail.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while self.opened_event(trail, csv, previous) is None:
                        await asyncio.sleep(0.01)
                # the deletion below must not be recorded
                await trail.watchdog.stop()
                records = [
                    event.to_record()
                    for event in trail.events.by_pos[:]
                ]
                csv.unlink()
                restored = Trail(root)
                assert restored.entries[csv].id == entry.id
                assert [
                    event.to_record()
                    for event in restored.events.by_pos[:]
                ] == records
                await restored.watchdog.stop()

        asyncio.run(run())

    def test_by_pos_slicing_after_tracking_changes(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            other_csv = root / 'other.csv'
            other_csv.write_text('name,value\nother,7\n', encoding='utf-8')
            trail = Trail()
            positions = trail.events.by_pos
            assert positions[:] == []
            trail.track(csv)
            first = positions[-1]
            trail.track(other_csv)
            second = positions[-1]
            trail.untrack(csv)
            third = positions[-1]
            trail.track(csv)
            fourth = positions[-1]

            assert positions[0] is first
            assert positions[-1] is fourth
            assert positions[:] == [first, second, third, fourth]
            assert positions[1:3] == [second, third]
            assert positions[:-1] == [first, second, third]
            assert positions[::2] == [first, third]
            assert positions[::-1] == [fourth, third, second, first]
            assert positions[10:] == []
            assert len(positions) == len(trail.events)
            assert trail.events.ids == [first.id, second.id, third.id, fourth.id]
            assert trail.events[first.id] is positions[0]
            assert isinstance(third, RemoveEntryEvent)

            trail.track(csv)
            assert positions[:] == [first, second, third, fourth]
            trail.untrack(other_csv)
            assert len(positions) == 5
            assert isinstance(positions[-1], RemoveEntryEvent)
            assert positions[-1].entry is second.entry
            assert trail.events.ids[-1] == positions[-1].id

            try:
                positions[10]
            except IndexError:
                pass
            else:
                raise AssertionError('out-of-range positions must raise IndexError')

            try:
                positions[::0]
            except ValueError:
                pass
            else:
                raise AssertionError('zero slice steps must raise ValueError')

    def test_track_and_untrack_return_entries_or_lists(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            other_csv = root / 'other.csv'
            other_csv.write_text('name,value\nother,7\n', encoding='utf-8')
            missing_csv = root / 'missing.csv'
            trail = Trail()

            assert trail.track() == []
            assert trail.untrack() == []
            assert trail.untrack(missing_csv) == []
            first = trail.track(csv)
            assert first is trail.entries[csv]
            assert trail.track(csv) is first
            assert len(trail.events) == 1
            assert trail.track(csv, csv) == [first]
            assert len(trail.events) == 1

            added = trail.track(other_csv, csv, other_csv)
            second = trail.entries[other_csv]
            assert isinstance(added, list)
            assert added == [second, first]
            assert len(trail.events) == 2
            assert trail.untrack(csv) is first
            assert trail.untrack(csv) == []
            assert len(trail.events) == 3
            assert trail.untrack(missing_csv, other_csv) == [second]
            assert len(trail.entries) == 0

            readded = trail.track(csv, other_csv)
            assert isinstance(readded, list)
            assert readded == [trail.entries[csv], trail.entries[other_csv]]
            removed = trail.untrack(other_csv, csv, other_csv)
            assert isinstance(removed, list)
            assert removed == readded[::-1]
            assert len(trail.entries) == 0

    def test_by_pos_survives_file_changes_and_reload(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / 'dataset.csv'
                trail = Trail(root)
                entry = trail.track(csv)
                previous = len(trail.events)
                with csv.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nexample,42\n'
                async with asyncio.timeout(5):
                    while self.opened_event(trail, csv, previous) is None:
                        await asyncio.sleep(0.01)
                previous = len(trail.events)
                csv.write_text('name,value\nexample,43\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while not any(
                        event.event_type == 'modified'
                        and event.src_path == str(csv)
                        for event in trail.events.by_pos[previous:]
                    ):
                        await asyncio.sleep(0.01)
                previous = len(trail.events)
                csv.unlink()
                async with asyncio.timeout(5):
                    while not any(
                        event.event_type == 'deleted'
                        and event.src_path == str(csv)
                        for event in trail.events.by_pos[previous:]
                    ):
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()

                assert trail.events.by_pos[-1].event_type == 'deleted'
                assert all(
                    event.entry is entry
                    for event in trail.events.by_pos[:]
                )
                history = trail.events.jsonl.path.read_bytes()
                expected_ids = trail.events.ids.copy()
                restored = Trail(root)
                assert restored.events.ids == expected_ids
                assert restored.events.by_pos[0].id == expected_ids[0]
                assert restored.events.by_pos[-1].id == expected_ids[-1]
                assert [
                    event.id
                    for event in restored.events.by_pos[::-1]
                ] == expected_ids[::-1]
                assert restored.entries[csv].id == entry.id
                restored.events.jsonl.read()
                assert restored.events.ids == expected_ids
                assert restored.events.jsonl.path.read_bytes() == history
                await restored.watchdog.stop()

        asyncio.run(run())

    def test_entry_positions_follow_tracking_changes(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            other_csv = root / 'other.csv'
            other_csv.write_text('name,value\nother,7\n', encoding='utf-8')
            directory = root / 'subdirectory'
            directory.mkdir()
            trail = Trail()
            files = trail.assets.by_pos
            directories = trail.dirs.by_pos
            entries = trail.entries.by_pos
            first = trail.track(csv)
            second = trail.track(other_csv)
            tracked_directory = trail.track(directory)

            assert files[:] == [first, second]
            assert files[::-1] == [second, first]
            assert directories[:] == [tracked_directory]
            assert entries[:] == [first, second, tracked_directory]
            assert trail.assets.ids == [first.id, second.id]
            assert trail.dirs.ids == [tracked_directory.id]
            assert trail.assets[first.id] is files[0]
            assert trail.assets[csv] is files[0]
            assert trail.track(csv) is first
            assert files[:] == [first, second]

            trail.untrack(csv)
            assert files[:] == [second]
            assert trail.assets.ids == [second.id]
            readded = trail.track(csv)
            assert readded.id != first.id
            assert files[:] == [second, readded]
            assert trail.assets.ids == [second.id, readded.id]
            assert entries[:] == [second, readded, tracked_directory]
            trail.untrack(other_csv, csv, directory)
            assert files[:] == []
            assert directories[:] == []
            assert entries[:] == []
            assert trail.assets.ids == []
            assert trail.dirs.ids == []

    def test_discovered_entries_survive_reload(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                root_entry = trail.track(root)
                csv = root / 'discovered.csv'
                directory = root / 'discovered_directory'
                csv.write_text('name,value\nother,7\n', encoding='utf-8')
                directory.mkdir()
                async with asyncio.timeout(5):
                    while (
                        csv not in trail.assets
                        or directory not in trail.dirs
                    ):
                        await asyncio.sleep(0.01)
                # the removals below must not be recorded
                await trail.watchdog.stop()

                file_entry = trail.assets[csv]
                directory_entry = trail.dirs[directory]
                assert trail.assets.by_pos[:] == [file_entry]
                assert trail.assets.ids == [file_entry.id]
                assert trail.dirs.by_pos[:] == [root_entry, directory_entry]
                assert trail.dirs.ids == [root_entry.id, directory_entry.id]
                csv.unlink()
                directory.rmdir()
                history = trail.events.jsonl.path.read_bytes()
                restored = Trail(root)
                assert restored.assets.ids == trail.assets.ids
                assert restored.dirs.ids == trail.dirs.ids
                assert restored.assets.by_pos[0] is restored.assets[csv]
                assert restored.assets.by_pos[0].id == file_entry.id
                assert restored.dirs.by_pos[-1].id == directory_entry.id
                assert restored.entries.by_pos[-1] is restored.dirs[directory]
                assert restored.events.jsonl.path.read_bytes() == history
                restored.untrack(csv, directory)
                assert restored.assets.ids == []
                assert restored.dirs.ids == [root_entry.id]
                reloaded = Trail(root)
                assert reloaded.assets.by_pos[:] == []
                assert reloaded.dirs.ids == [root_entry.id]
                await restored.watchdog.stop()
                await reloaded.watchdog.stop()

        asyncio.run(run())

    def test_hex_ids_support_lookup_and_persistence(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            trail = Trail(root)
            entry = trail.track(csv)
            directory = trail.track(root)
            checkpoint = Checkpoint(events=trail.events.by_pos[:])
            identifiers = [
                trail.id,
                entry.id,
                directory.id,
                checkpoint.id,
                *trail.events.ids,
            ]
            for identifier in identifiers:
                assert isinstance(identifier, str)
                assert len(identifier) == 32
                assert set(identifier) <= set('0123456789abcdef')

            assert trail.entries[entry.id] is entry
            assert trail.entries[directory.id] is directory
            assert trail.assets[entry.id] is trail.assets[str(csv)]
            assert trail.dirs[directory.id] is trail.dirs[root]
            assert entry.id in trail.assets
            assert directory.id in trail.dirs
            assert trail.entries[[entry.id, directory.id]] == (entry, directory)
            checkpoint_record = checkpoint.to_record()
            assert checkpoint_record['events'] == trail.events.ids
            restored_checkpoint = Checkpoint.from_record(**checkpoint_record)
            assert restored_checkpoint.to_record() == checkpoint_record

            restored = Trail(root)
            assert restored.id == trail.id
            assert restored.entries[entry.id].path == csv
            assert restored.entries[directory.id].path == root
            assert restored.events.ids == trail.events.ids

    def test_legacy_integer_ids_are_normalized_on_reload(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            trail = Trail(root)
            entry = trail.track(csv)
            event = trail.events.by_pos[0]
            legacy_metadata = {'id': int(trail.id, 16)}
            trail.json.path.write_text(json.dumps(legacy_metadata), encoding='utf-8')
            legacy_event = event.to_record()
            legacy_event['id'] = int(event.id, 16)
            legacy_event['entry'] = int(entry.id, 16)
            trail.events.jsonl.path.write_text(
                json.dumps(legacy_event) + '\n',
                encoding='utf-8',
            )

            restored = Trail(root)
            assert restored.id == trail.id
            assert restored.assets.ids == [entry.id]
            assert restored.events.ids == [event.id]
            assert restored.entries[entry.id] is restored.assets.by_pos[0]
            assert restored.events[event.id].entry is restored.entries[entry.id]
            restored.untrack(csv)
            reloaded = Trail(root)
            assert reloaded.id == trail.id
            assert csv not in reloaded.entries
            assert reloaded.events.ids == restored.events.ids

    def test_folder_auto_tracks_created_files_and_nested_subdirectories(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                folder = root / 'folder'
                folder.mkdir()
                folder_entry = trail.track(folder)

                new_file = folder / 'new.csv'
                new_file.write_text('name,value\nnew,1\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while new_file not in trail.assets:
                        await asyncio.sleep(0.01)
                new_file_entry = trail.assets[new_file]

                previous = len(trail.events)
                with new_file.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nnew,1\n'
                async with asyncio.timeout(5):
                    while (
                        opened := self.opened_event(trail, new_file, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                assert opened.entry is new_file_entry

                subdirectory = folder / 'nested'
                subdirectory.mkdir()
                async with asyncio.timeout(5):
                    while subdirectory not in trail.dirs:
                        await asyncio.sleep(0.01)
                subdirectory_entry = trail.dirs[subdirectory]

                nested_file = subdirectory / 'nested.csv'
                nested_file.write_text('name,value\nnested,2\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while nested_file not in trail.assets:
                        await asyncio.sleep(0.01)
                nested_file_entry = trail.assets[nested_file]
                await trail.watchdog.stop()

                assert folder_entry.path == folder
                assert new_file_entry.path == new_file
                assert subdirectory_entry.path == subdirectory
                assert nested_file_entry.path == nested_file

        asyncio.run(run())


    def test_renamed_file_keeps_its_resource_id(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / 'dataset.csv'
                trail = Trail(root)
                entry = trail.track(csv)
                identifier = entry.id
                renamed = root / 'renamed.csv'
                previous = len(trail.events)
                csv.rename(renamed)
                async with asyncio.timeout(5):
                    while not any(
                        event.event_type == 'moved'
                        and event.dest_path == str(renamed)
                        for event in trail.events.by_pos[previous:]
                    ):
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()

                assert trail.assets[renamed] is entry
                assert entry.id == identifier
                assert entry.path == renamed
                assert csv not in trail.assets
                assert trail.assets.ids == [identifier]

                restored = Trail(root)
                assert restored.assets.ids == [identifier]
                assert restored.assets[renamed].id == identifier
                assert csv not in restored.assets
                await restored.watchdog.stop()

        asyncio.run(run())

    def test_renamed_folder_repaths_its_contents(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                folder = root / 'folder'
                folder.mkdir()
                folder_entry = trail.track(folder)
                nested = folder / 'nested'
                nested.mkdir()
                async with asyncio.timeout(5):
                    while nested not in trail.dirs:
                        await asyncio.sleep(0.01)
                nested_entry = trail.dirs[nested]
                inner = nested / 'inner.csv'
                inner.write_text('name,value\ninner,1\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while inner not in trail.assets:
                        await asyncio.sleep(0.01)
                inner_entry = trail.assets[inner]

                renamed = root / 'renamed'
                folder.rename(renamed)
                async with asyncio.timeout(5):
                    while renamed not in trail.dirs:
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()

                assert trail.dirs[renamed] is folder_entry
                assert trail.dirs[renamed / 'nested'] is nested_entry
                assert trail.assets[renamed / 'nested' / 'inner.csv'] is inner_entry
                assert folder not in trail.dirs
                assert inner not in trail.assets

                restored = Trail(root)
                assert restored.dirs.ids == trail.dirs.ids
                assert restored.assets.ids == trail.assets.ids
                assert restored.assets[renamed / 'nested' / 'inner.csv'].id == inner_entry.id
                await restored.watchdog.stop()

        asyncio.run(run())


if __name__ == "__main__":
    if sys.platform != "linux":
        raise SystemExit("file opening requires Linux inotify")

    test_object = TestTrail()
    tests = [
        ("test_pathless_track_and_open", "pathless trail tracks an opened CSV"),
        (
            'test_passive_instantiation_observes_without_a_context',
            'passive instantiation observes without a context',
        ),
        (
            'test_passive_instantiation_is_dormant_without_a_loop',
            'passive instantiation stays dormant without a loop',
        ),
        (
            'test_watchdog_context_starts_and_stops_the_session',
            'the watchdog context starts and stops the session',
        ),
        (
            'test_watchdog_context_adopts_a_passive_session',
            'the watchdog context adopts a passive session',
        ),
        (
            "test_persistent_track_open_and_new_session",
            "persistent trail survives new sessions",
        ),
        (
            "test_untracked_file_stays_untracked_after_new_session",
            "untracked files stay untracked",
        ),
        (
            "test_deleted_csv_history_can_be_restored",
            "deleted CSV history is restored",
        ),
        (
            "test_track_untrack_and_retrack_history_can_be_restored",
            "tracking changes replay with stable entry identities",
        ),
        (
            'test_by_pos_slicing_after_tracking_changes',
            'positional slicing follows real tracking changes',
        ),
        (
            'test_by_pos_survives_file_changes_and_reload',
            'positional indexing survives real file changes and reload',
        ),
        (
            'test_track_and_untrack_return_entries_or_lists',
            'single paths return entries and multiple paths return lists',
        ),
        (
            'test_entry_positions_follow_tracking_changes',
            'entry positions follow tracking changes',
        ),
        (
            'test_discovered_entries_survive_reload',
            'discovered entries retain positions and identities after reload',
        ),
        (
            'test_hex_ids_support_lookup_and_persistence',
            'hexadecimal IDs support lookup and persistence',
        ),
        (
            'test_legacy_integer_ids_are_normalized_on_reload',
            'legacy integer IDs normalize to hexadecimal strings on reload',
        ),
        (
            'test_folder_auto_tracks_created_files_and_nested_subdirectories',
            'folders auto-track created files and nested subdirectories',
        ),
        (
            'test_renamed_file_keeps_its_resource_id',
            'renamed files keep their resource ID across a reload',
        ),
        (
            'test_renamed_folder_repaths_its_contents',
            'renamed folders repath their tracked contents',
        ),
    ]

    for method_name, description in tests:
        getattr(test_object, method_name)()
        print(f"  ✓ {description}")
