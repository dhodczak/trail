from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from functools import cached_property
from pathlib import Path
from typing import overload
from uuid import uuid4

from trail.asset import Assets
from trail.dir import Dirs
from trail.entry import Entry, EntryKey
from trail.event import AddEntryEvent, Events, RemoveEntryEvent
from trail.node import Node
from trail.util import ByPos, PathLike, asset_repr, items_repr, list_repr, normalize_id
from trail.watchdog import Watchdog


class JSON(Node):
    _parent: Trail

    def __repr__(self) -> str:
        return asset_repr(type(self).__name__, self.path)

    @property
    def record(self) -> dict:
        trail = self._parent
        out = {
            "id": trail.id,
        }
        return out

    def dump(self):
        """Dump the trail metadata to the JSON file."""
        path = self.path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            json.dump(self.record, f)

    def load(self):
        """Load the trail metadata from the JSON file."""
        path = self.path
        if path is None or not path.exists():
            return
        with path.open("r") as f:
            data = json.load(f)
        trail = self._parent
        for key, value in data.items():
            if key == "id":
                value = normalize_id(value)
            self._setnested(trail, key, value)

    @cached_property
    def path(self):
        """Determines the trail metadata file path based on the trail directory."""
        trail = self._trail
        if trail.dir:
            return trail.dir / "project.json"
        else:
            return None


class EntryLookup(
    Mapping[EntryKey, Entry],
    Node,
):
    """
    Lookup class for filesystme entries in a Trail; wraps `Trail.assets` and `Trail.dirs` into a single mapping.
    """

    _parent: Trail
    _repr_name = "Entries"

    @property
    def ids(self) -> list[str]:
        return self._parent.assets.ids + self._parent.dirs.ids

    @cached_property
    def by_pos(self) -> ByPos[Entry]:
        return ByPos(self)

    def __repr__(self) -> str:
        return items_repr(
            self._repr_name,
            (self[identifier] for identifier in self.ids),
        )

    @overload
    def __getitem__(self, key: EntryKey) -> Entry: ...

    @overload
    def __getitem__(self, key: Iterable[EntryKey]) -> tuple[Entry, ...]: ...

    def __getitem__(
        self,
        key: EntryKey | Iterable[EntryKey],
    ) -> Entry | tuple[Entry, ...]:
        """Returns an Entry object (Asset, Dir) or a tuple of Entry objects based on the provided key(s)."""
        if isinstance(key, str):
            entry = self._parent.assets.id2entry.get(key)
            if entry is None:
                entry = self._parent.dirs.id2entry.get(key)
            if entry is not None:
                return entry
        if isinstance(key, (str, os.PathLike)):
            try:
                return self._parent.assets[key]
            except KeyError:
                return self._parent.dirs[key]
        selected = []
        for value in key:
            if not isinstance(value, (str, os.PathLike)):
                raise TypeError("Expected a path or entry ID")
            selected.append(self[value])
        return tuple(selected)

    def __iter__(self) -> Iterator[str]:
        """Iterates across all Entry IDs in the Trail, including both assets and directories."""
        yield from self._parent.assets
        yield from self._parent.dirs

    def items(self):
        """Returns an iterator over (Entry ID, Entry) pairs for all entries in the Trail."""
        yield from self._parent.assets.items()
        yield from self._parent.dirs.items()

    def __len__(self) -> int:
        """Returns the total number of entries in the Trail, including both assets and directories."""
        out = len(self._parent.assets)
        out += len(self._parent.dirs)
        return out


class Trail(Node):
    """

    >>> trail
    Trail
    id: '6617127da84b49e481b612c0418244c5'
    dir: '/tmp/tmpfnmpus7h/.trail'
    entries: [
        '/tmp/tmpfnmpus7h/folder/new.csv',
        '/tmp/tmpfnmpus7h/folder/nested/nested.csv',
        '/tmp/tmpfnmpus7h/folder',
        '/tmp/tmpfnmpus7h/folder/nested',
    ]
    """

    # paths listed by __repr__ before the remainder is summarized
    repr_limit = 10

    @cached_property
    def watchdog(self):
        """
        Returns a Watchdog instance, which wraps the `watchdog` library's functionality
        for monitoring filesystem events.

        >>> self.watchdog
        Watchdog
            running: True
            watches: [
                '/tmp/tmpbzh09nb5/folder',
                '/tmp/tmpbzh09nb5',
                '/tmp/tmpbzh09nb5/folder/nested',
            ]
        """
        return Watchdog(self)

    @cached_property
    def assets(self):
        """
        Returns a Assets instance, which contains the mapping of IDs and paths to tracked Asset entries in the Trail.

        >>> self.assets
        Assets (2)
            0. Asset
                id: '41d3f259a5fc4c1fa13c516cf892f56e'
                path: '/tmp/tmpbzh09nb5/folder/new.csv'
            1. Asset
                id: '6e564e209ff44bafa32cf75d9ffcd844'
                path: '/tmp/tmpbzh09nb5/folder/nested/nested.csv'
        """
        return Assets(self)

    @cached_property
    def dirs(self):
        """
        Returns a Dirs instance, which contains the mapping of IDs and paths to tracked Dir entries in the Trail.

        >>> self.dirs
        Dirs (2)
            0. Dir
                id: 'decbe4d041fa4c1893da693c70ad9105'
                path: '/tmp/tmpbzh09nb5/folder'
            1. Dir
                id: 'f48807577f1d454a9caa6814af452d8e'
                path: '/tmp/tmpbzh09nb5/folder/nested'
        """
        return Dirs(self)

    @cached_property
    def entries(self) -> EntryLookup:
        """
        Returns an EntryLookup, which provides a unified interface to access both Asset and Dir entries in the Trail.

        >>> self.entries
        Entries (4)
            0. Asset
                id: '41d3f259a5fc4c1fa13c516cf892f56e'
                path: '/tmp/tmpbzh09nb5/folder/new.csv'
            1. Asset
                id: '6e564e209ff44bafa32cf75d9ffcd844'
                path: '/tmp/tmpbzh09nb5/folder/nested/nested.csv'
            2. Dir
                id: 'decbe4d041fa4c1893da693c70ad9105'
                path: '/tmp/tmpbzh09nb5/folder'
            3. Dir
                id: 'f48807577f1d454a9caa6814af452d8e'
                path: '/tmp/tmpbzh09nb5/folder/nested'
        """
        return EntryLookup(self)

    @cached_property
    def _unregistered_paths(self) -> set[Path]:
        return set()

    @cached_property
    def json(self):
        """Returns a JSON instance, which functions as a namespace for the Trail's metadata stored in a JSON file."""
        return JSON(self)

    @cached_property
    def events(self):
        """
        Returns an Events instance, which manages the collection of events that have occurred in the Trail.

        >>> self.events
        Events (9)
            0. AddEntryEvent
                id: '8a43da699bf242e7976e7bb74df81d0f'
                timestamp: '06:02:12.114'
                src_path: '/tmp/tmpbzh09nb5/folder'
            1. WatchdogEvent
                id: 'c2152a8b8f31416abbfe7106fce8cb6c'
                timestamp: '06:02:12.130'
                src_path: '/tmp/tmpbzh09nb5/folder/new.csv'
                event_type: 'created'
            2. WatchdogEvent
                id: '620c0779453a4e5db975fa11e9efac7b'
                timestamp: '06:02:12.130'
                src_path: '/tmp/tmpbzh09nb5/folder/new.csv'
                event_type: 'opened'
        """
        return Events(self)

    def __init__(
        self,
        dir: PathLike | None = None,
    ) -> None:
        """TODO: reference Myst's setup for a Trail setup"""
        super().__init__()
        if dir is None:
            # nodir mode
            self.dir = None
        else:
            dir = Path(dir).expanduser().resolve()
            if dir.name != ".trail":
                dir /= ".trail"
            self.dir = dir
            self.json.load()
            self.events.jsonl.read()
            self.json.dump()

    @cached_property
    def id(self) -> str:
        """
        Assigns a unique identifier to the Trail instance using a UUID4 hex string.
        Performed as a lazy attribute so that `self.json.load()` may take precedence.
        """
        return uuid4().hex

    def register(self, *paths: PathLike) -> Entry | list[Entry]:
        """Registers the specified filesystem paths with the Trail for tracking."""
        requested = dict.fromkeys(Path(path).expanduser().resolve() for path in paths)
        for path in requested:
            if self._ignored(path):
                raise ValueError(f"Cannot track Trail metadata: {path}")
            if path not in self.entries:
                Entry.from_path(path, trail=self)
        registered = []
        for path in requested:
            entry = self.entries.get(path)
            if entry is None:
                event = AddEntryEvent(src_path=str(path))
                entry = event.apply(self)
                self.events[event.id] = event
            registered.append(entry)
        if len(paths) == 1:
            return registered[0]
        return registered

    def _ignored(self, path: Path) -> bool:
        """Returns True if the path is relative to the `trail` directory, e.g. `/.trail/ignored"""
        return self.dir is not None and path.is_relative_to(self.dir)

    def unregister(self, *paths: PathLike) -> Entry | list[Entry]:
        """Unregisters the specified filesystem paths from the Trail."""
        requested = dict.fromkeys(Path(path).expanduser().resolve() for path in paths)
        unregistered = []
        for path in requested:
            entry = self.entries.get(path)
            if entry is None:
                continue
            event = RemoveEntryEvent(src_path=str(path))
            result = event.apply(self)
            if result is not None:
                self.events[event.id] = event
                unregistered.append(result)
        if len(paths) == 1 and unregistered:
            return unregistered[0]
        return unregistered

    def push(self):
        """Placeholder for possible remote synchronization"""

    def pull(self):
        """Placeholder for possible remote synchronization"""

    def __repr__(self) -> str:
        if self.dir is None:
            directory = None
        else:
            directory = str(self.dir)
        lines = [
            type(self).__name__,
            f"    id: {self.id!r}",
            f"    dir: {directory!r}",
        ]
        entries = self.entries
        identifiers = entries.ids
        lines.extend(
            list_repr(
                "entries",
                (str(entries[identifier].path) for identifier in identifiers[: self.repr_limit]),
                len(identifiers),
            )
        )
        return "\n".join(lines)
