from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Self
from uuid import uuid4

from trail.entry import Entries, Entry


class Asset(Entry):
    """
    Represents a file entry tracked by a Trail.

    >>> trail.assets.by_pos[0]
    Asset
        id: '41d3f259a5fc4c1fa13c516cf892f56e'
        path: '/tmp/tmpbzh09nb5/folder/new.csv'
    """
    _parent: Assets

    @cached_property
    def _parent(self) -> Assets:
        return self._trail.assets

    @property
    def _watch_paths(self) -> tuple[Path, ...]:
        return (self.directory,)

    # @property
    # def events(self) -> Event:
    #     changes = self._trail.changes
    #     selected = (
    #         change
    #         for change in changes
    #         if change.asset_id == self.id
    #     )
    #     return Events(changes, selected)

    def track(self) -> Self:
        collection = self._parent
        if collection is None:
            raise ValueError("Entry has no Trail; pass trail to from_path")
        trail = collection._trail
        if self._trail is not trail:
            raise ValueError("Entry already belongs to another Trail")
        if collection is not trail.assets:
            raise ValueError("Entry belongs to the wrong collection")
        path = Path(self.path).expanduser().resolve()
        if trail._ignored(path):
            raise ValueError(f"Cannot track Trail metadata: {path}")
        while self.id in trail.dirs.id2entry:
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

    def offtrail(self) -> None:
        if self._parent.id2entry.get(self.id) is not self:
            return
        self._watchdog.release(self.directory, self.id)
        super().offtrail()


class Assets(Entries[Asset]):
    """
    A collection of Asset entries tracked by a Trail.

    >>> trail.assets
    Assets (2)
        0. Asset
            id: '41d3f259a5fc4c1fa13c516cf892f56e'
            path: '/tmp/tmpbzh09nb5/folder/new.csv'
        1. Asset
            id: '6e564e209ff44bafa32cf75d9ffcd844'
            path: '/tmp/tmpbzh09nb5/folder/nested/nested.csv'
    """
    entry_type = Asset
