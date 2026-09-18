from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from functools import cached_property
from pathlib import Path
from typing import overload
from uuid import uuid4

from .dir import Dirs
from .entry import Entry, EntryKey
from .event import AddEntryEvent, Events, RemoveEntryEvent
from .file import Files
from .node import Node
from .watchdog import Watchdog


class JSON(Node):
    _parent: Trail

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
            self._setnested(trail, key, value)

    @cached_property
    def path(self):
        """Determines the trail metadata file path based on the trail directory."""
        trail = self._trail
        if trail.dir:
            return trail.dir / "path.json"
        else:
            return None


class EntryLookup(
    Mapping[EntryKey, Entry],
    Node,
):
    """
    Lookup class for filesystme entries in a Trail; wraps `Trail.files` and `Trail.dirs` into a single mapping.
    """

    _parent: Trail

    @overload
    def __getitem__(self, key: EntryKey) -> Entry: ...

    @overload
    def __getitem__(self, key: Iterable[EntryKey]) -> tuple[Entry, ...]: ...

    def __getitem__(
        self,
        key: EntryKey | Iterable[EntryKey],
    ) -> Entry | tuple[Entry, ...]:
        """Returns an Entry object (File, Dir) or a tuple of Entry objects based on the provided key(s)."""
        if isinstance(key, (str, Path, int)):
            try:
                return self._parent.files[key]
            except KeyError:
                return self._parent.dirs[key]
        selected = []
        for value in key:
            if not isinstance(value, (str, Path, int)):
                raise TypeError("Expected a path or entry ID")
            selected.append(self[value])
        return tuple(selected)

    def __iter__(self) -> Iterator[int]:
        """Iterates across all Entry IDs in the Trail, including both files and directories."""
        yield from self._parent.files
        yield from self._parent.dirs

    def items(self):
        """Returns an iterator over (Entry ID, Entry) pairs for all entries in the Trail."""
        yield from self._parent.files.items()
        yield from self._parent.dirs.items()

    def __len__(self) -> int:
        """Returns the total number of entries in the Trail, including both files and directories."""
        out = len(self._parent.files)
        out += len(self._parent.dirs)
        return out


class Trail(Node):
    @cached_property
    def watchdog(self):
        """
        Returns a Watchdog instance, which wraps the `watchdog` library's functionality
        for monitoring filesystem events.
        """
        return Watchdog(self)

    @cached_property
    def files(self):
        """
        Returns a Files instance, which contains the mapping of IDs and paths to tracked File entries in the Trail.
        """
        return Files(self)

    @cached_property
    def dirs(self):
        """
        Returns a Dirs instance, which contains the mapping of IDs and paths to tracked Dir entries in the Trail.
        """
        return Dirs(self)

    @cached_property
    def entries(self) -> EntryLookup:
        """
        Returns an EntryLookup, which provides a unified interface to access both File and Dir entries in the Trail.
        """
        return EntryLookup(self)

    @cached_property
    def _removed_paths(self) -> set[Path]:
        return set()

    @cached_property
    def json(self):
        """Returns a JSON instance, which functions as a namespace for the Trail's metadata stored in a JSON file."""
        return JSON(self)

    @cached_property
    def events(self):
        """Returns an Events instance, which manages the collection of events that have occurred in the Trail."""
        return Events(self)

    def __init__(
        self,
        dir: str | Path | None = None,
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
    def id(self) -> int:
        """
        Assigns a unique identifier to the Trail instance using a UUID4 integer.
        Performed as a lazy attribute so that `self.json.load()` may take precedence.
        """
        return uuid4().int

    def add(self, *paths: str | Path) -> Entry | list[Entry]:
        """Adds the specified filesystem paths to the Trail for tracking."""
        requested = dict.fromkeys(Path(path).expanduser().resolve() for path in paths)
        for path in requested:
            if self._ignored(path):
                raise ValueError(f"Cannot track Trail metadata: {path}")
            if path not in self.entries:
                Entry.from_path(path, trail=self)
        added = []
        for path in requested:
            entry = self.entries.get(path)
            if entry is None:
                event = AddEntryEvent(src_path=str(path))
                entry = event.apply(self)
                self.events[event.id] = event
            added.append(entry)
        if len(paths) == 1:
            return added[0]
        return added

    def _ignored(self, path: Path) -> bool:
        """Returns True if the path is relative to the `trail` directory, e.g. `/.trail/ignored"""
        return self.dir is not None and path.is_relative_to(self.dir)

    def remove(self, *paths: str | Path) -> Entry | list[Entry]:
        """Removes the specified filesystem paths from the Trail."""
        requested = dict.fromkeys(Path(path).expanduser().resolve() for path in paths)
        removed = []
        for path in requested:
            entry = self.entries.get(path)
            if entry is None:
                continue
            event = RemoveEntryEvent(src_path=str(path))
            result = event.apply(self)
            if result is not None:
                self.events[event.id] = event
                removed.append(result)
        if len(paths) == 1 and removed:
            return removed[0]
        return removed

    def push(self):
        """Placeholder for possible remote synchronization"""

    def pull(self):
        """Placeholder for possible remote synchronization"""
