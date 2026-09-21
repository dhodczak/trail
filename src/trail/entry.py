from __future__ import annotations

import os
from collections.abc import ItemsView, Iterable, Iterator
from dataclasses import dataclass, field, fields
from functools import cached_property
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import TYPE_CHECKING, Self, overload
from uuid import uuid4

from trail.node import Node
from trail.util import ByPos, PathLike, items_repr, normalize_id

if TYPE_CHECKING:
    from trail.event import Event
    from trail.trail import Trail

# an entry is addressed by its hexadecimal ID or by its path
EntryKey = PathLike


@dataclass(kw_only=True, eq=False, repr=False)
class Entry(Node):
    """Represents a file system entry (file or directory) tracked by a Trail."""
    id: str = field(default_factory=lambda: uuid4().hex)
    path: Path

    if TYPE_CHECKING:
        _parent: Entries[Self]

    def __post_init__(self) -> None:
        Node.__init__(self)
        self.id = normalize_id(self.id)
        self.path = Path(self.path).expanduser().resolve()

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        for entry_field in fields(self):
            if not entry_field.repr:
                continue
            value = getattr(self, entry_field.name)
            if entry_field.name == 'path':
                value = str(value)
            yield entry_field.name, value

    def __repr__(self) -> str:
        lines = [type(self).__name__]
        lines.extend(
            f'    {name}: {value!r}'
            for name, value in self._repr_items()
        )
        return '\n'.join(lines)

    @classmethod
    def from_path(
        cls,
        path: PathLike,
        trail: Trail | None = None,
    ) -> Self:
        from trail.asset import Asset
        from trail.dir import Dir

        path = Path(path).expanduser().resolve()
        metadata = path.stat()

        if trail is not None and trail._ignored(path):
            raise ValueError(f"Cannot track Trail metadata: {path}")

        if issubclass(cls, Dir):
            if not S_ISDIR(metadata.st_mode):
                raise ValueError(f"Not a directory: {path}")
        elif issubclass(cls, Asset):
            if not S_ISREG(metadata.st_mode):
                raise ValueError(f"Not a regular file: {path}")
        else:
            if S_ISDIR(metadata.st_mode):
                cls = Dir
            elif S_ISREG(metadata.st_mode):
                cls = Asset
            else:
                raise ValueError(f"Not a regular file or directory: {path}")

        out = cls(path=path)
        out._trail = trail
        if trail is not None:
            if isinstance(out, Dir):
                out._parent = trail.dirs
            else:
                out._parent = trail.assets
        return out

    @property
    def name(self) -> str:
        """Name of the entry (last component of the path)."""
        return self.path.name

    @property
    def directory(self) -> Path:
        """Directory containing the entry."""
        return self.path.parent

    @cached_property
    def size(self) -> int:
        """Size of the entry, in bytes."""
        return self.path.stat().st_size

    @cached_property
    def mtime(self) -> float:
        """Last modification time of the entry, in seconds since the epoch."""
        return self.path.stat().st_mtime

    @cached_property
    def events(self) -> dict[str, Event]:
        """Events associated with the entry."""
        return {}

    @property
    def _watch_paths(self) -> tuple[Path, ...]:
        raise NotImplementedError

    def walk(self) -> Iterator[Entry]:
        yield self

    def register(self) -> Self:
        raise NotImplementedError

    def move(self, destination: PathLike) -> Self:
        """
        Repoint a tracked entry at `destination`, keeping its ID and its position in the
        collection, so that renaming a resource outside the process does not fork its identity.
        """
        collection = self._parent
        destination = Path(destination).expanduser().resolve()
        if destination == self.path:
            return self
        trail = self._trail
        if trail._ignored(destination):
            raise ValueError(f"Cannot track Trail metadata: {destination}")
        watchdog = self._watchdog
        previous_path = self.path
        previous_watches = self._watch_paths
        self.path = destination
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
        for watched_path in previous_watches:
            if watched_path not in self._watch_paths:
                watchdog.release(watched_path, self.id)
        if collection.path2entry.get(previous_path) is self:
            del collection.path2entry[previous_path]
        occupant = collection.path2entry.get(destination)
        if (
            occupant is not None
            and occupant is not self
        ):
            occupant.unregister()
        collection.path2entry[destination] = self
        if self.id not in collection.id2entry:
            collection.ids.append(self.id)
            collection.id2entry[self.id] = self
        return self

    def unregister(self) -> None:
        """Unregister the entry from the tracked Entries collection."""
        collection = self._parent
        if collection is None or collection.id2entry.get(self.id) is not self:
            return
        del collection.id2entry[self.id]
        collection.ids.remove(self.id)
        if collection.path2entry.get(self.path) is self:
            del collection.path2entry[self.path]


class Entries[E: Entry](Node):
    """
    A collection of Entry objects (Asset or Dir) tracked by a Trail.

    >>> trail.entries
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
    _parent: Trail
    entry_type: type[E]

    def __init__(self, parent: Trail | None = None) -> None:
        Node.__init__(self, parent)
        self.ids: list[str] = []

    @cached_property
    def by_pos(self) -> ByPos[E]:
        return ByPos(self)

    def __repr__(self) -> str:
        return items_repr(
            type(self).__name__,
            (
                self[identifier]
                for identifier in self.ids
            ),
        )

    @cached_property
    def path2entry(self) -> dict[Path, E]:
        return {}

    @cached_property
    def id2entry(self) -> dict[str, E]:
        return {}

    @overload
    def __getitem__(self, key: EntryKey) -> E: ...

    @overload
    def __getitem__(self, key: Iterable[EntryKey]) -> tuple[E, ...]: ...

    def __getitem__(
        self,
        key: EntryKey | Iterable[EntryKey],
    ) -> E | tuple[E, ...]:
        if isinstance(key, str) and key in self.id2entry:
            return self.id2entry[key]
        if isinstance(key, (str, os.PathLike)):
            return self.path2entry[Path(key).expanduser().resolve()]
        selected = []
        for value in key:
            if not isinstance(value, (str, os.PathLike)):
                raise TypeError("Expected a path or entry ID")
            selected.append(self[value])
        return tuple(selected)

    def __iter__(self) -> Iterator[str]:
        return iter(self.id2entry)

    def __len__(self) -> int:
        return len(self.id2entry)

    def __contains__(self, key: EntryKey) -> bool:
        if isinstance(key, str) and key in self.id2entry:
            return True
        return Path(key).expanduser().resolve() in self.path2entry

    @overload
    def get(self, key: EntryKey) -> E | None: ...

    @overload
    def get[D](
        self,
        key: EntryKey,
        default: D,
    ) -> E | D: ...

    def get[D](
        self,
        key: EntryKey,
        default: D | None = None,
    ) -> E | D | None:
        try:
            return self[key]
        except KeyError:
            return default

    def items(self) -> ItemsView[str, E]:
        return self.id2entry.items()

    def clear(self) -> None:
        """
        Unregister everything the collection holds. The removals are recorded like any other, so
        that a cleared collection stays cleared rather than coming back with the log's replay.
        """
        paths = tuple(
            self.id2entry[identifier].path
            for identifier in self.ids
        )
        if not paths:
            return
        self._trail.unregister(*paths)

    def entry(self, *paths: PathLike) -> tuple[E, ...]:
        selected: dict[Path, E] = {}
        for path in paths:
            resolved = Path(path).expanduser().resolve()
            if resolved not in selected:
                selected[resolved] = self.get(resolved) or self.entry_type.from_path(
                    resolved, trail=self._trail
                )
        registered = []
        try:
            for entry in selected.values():
                if self.id2entry.get(entry.id) is entry:
                    entry.register()
                    continue
                while (
                    entry.id in self._trail.assets
                    or entry.id in self._trail.dirs
                ):
                    entry.id = uuid4().hex
                entry.register()
                registered.append(entry)
        except Exception:
            for entry in reversed(registered):
                entry.unregister()
            raise
        return tuple(selected.values())
