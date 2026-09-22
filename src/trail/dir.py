from __future__ import annotations

from collections.abc import Iterator
from functools import cached_property
from pathlib import Path
from typing import Self
from uuid import uuid4

from trail.entry import Entries, Entry
from trail.util import PathLike


class Dir(Entry):
    """
    Represents a directory entry tracked by a Trail.

    >>> trail.dirs.by_pos[0]
    Dir
        id: 'decbe4d041fa4c1893da693c70ad9105'
        path: '/tmp/tmpbzh09nb5/folder'
    """
    _parent: Dirs

    @cached_property
    def _parent(self) -> Dirs:
        return self._trail.dirs

    @property
    def _watch_paths(self) -> tuple[Path, ...]:
        return tuple(dict.fromkeys((self.path, self.directory)))

    # @property
    # def events(self) -> Events:
    #     changes = self._trail.changes
    #     selected = (
    #         change
    #         for change in changes
    #         if change.dir_id == self.id
    #     )
    #     return Changes(changes, selected)

    def track(self) -> Self:
        collection = self._parent
        if collection is None:
            raise ValueError("Entry has no Trail; pass trail to from_path")
        trail = collection._trail
        if self._trail is not trail:
            raise ValueError("Entry already belongs to another Trail")
        if collection is not trail.dirs:
            raise ValueError("Entry belongs to the wrong collection")
        path = Path(self.path).expanduser().resolve()
        if trail._ignored(path):
            raise ValueError(f"Cannot track Trail metadata: {path}")
        while self.id in trail.assets.id2entry:
            self.id = uuid4().hex
        previous_path = self.path
        self.path = path
        watchdog = self._watchdog
        retained = []
        try:
            for watched_path in self._watch_paths:
                watchdog.watch(watched_path)
                ids = watchdog.dir2ids.setdefault(watched_path, set())
                if self.id not in ids:
                    ids.add(self.id)
                    retained.append(watched_path)
        except Exception:
            for watched_path in reversed(retained):
                watchdog.release(watched_path, self.id)
            self.path = previous_path
            raise

        previous = collection.id2entry.get(self.id)
        occupant = collection.path2entry.get(path)
        for old in (previous, occupant):
            if (
                old is None
                or old is self
                # entries contains fresh entry
                or collection.id2entry.get(old.id) is not old
            ):
                continue
            if old.id == self.id:
                for watched_path in old._watch_paths:
                    if watched_path not in self._watch_paths:
                        watchdog.release(watched_path, old.id)
                Entry.offtrail(old)
            else:
                old.offtrail()
        collection.path2entry[path] = self
        if self.id not in collection.id2entry:
            collection.ids.append(self.id)
        collection.id2entry[self.id] = self
        return self

    def move(self, destination: PathLike) -> Self:
        previous_path = self.path
        super().move(destination)
        trail = self._trail
        descendants = (
            entry
            for collection in (trail.assets, trail.dirs)
            for entry in tuple(collection.id2entry.values())
            if entry is not self and entry.path.is_relative_to(previous_path)
        )
        for entry in descendants:
            entry.move(self.path / entry.path.relative_to(previous_path))
        return self

    def walk(self) -> Iterator[Entry]:
        trail = self._trail
        pending: list[Entry] = [self]
        seen: set[Path] = set()
        while pending:
            entry = pending.pop()
            if entry.path in seen:
                continue
            seen.add(entry.path)
            yield entry
            if not isinstance(entry, Dir) or not entry.path.is_dir():
                continue
            for child in entry.path.iterdir():
                if (
                    child.is_symlink()
                    or trail._ignored(child)
                    or child in trail._offtrailed_paths
                ):
                    continue
                if child.is_dir():
                    collection = trail.dirs
                else:
                    collection = trail.assets
                pending.append(
                    collection.get(child) or Entry.from_path(child, trail=trail)
                )

    def offtrail(self) -> None:
        if self._parent.id2entry.get(self.id) is not self:
            return
        for path in self._watch_paths:
            self._watchdog.release(path, self.id)
        super().offtrail()


class Dirs(Entries[Dir]):
    """
    A collection of Dir entries tracked by a Trail.

    >>> trail.dirs
    Dirs (2)
        0. Dir
            id: 'decbe4d041fa4c1893da693c70ad9105'
            path: '/tmp/tmpbzh09nb5/folder'
        1. Dir
            id: 'f48807577f1d454a9caa6814af452d8e'
            path: '/tmp/tmpbzh09nb5/folder/nested'
    """
    entry_type = Dir
