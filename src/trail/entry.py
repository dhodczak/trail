from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, fields
from functools import cached_property
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import TYPE_CHECKING, Self, overload
from uuid import uuid4

from trail.collection import Collection
from trail.node import Node
from trail.util import (
    MISSING,
    PathLike,
    bare_repr,
    mtime_repr,
    normalize_id,
    st_size_repr,
)

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
        # both are stat'd on access rather than stored, so a resource that has gone says so
        # rather than raising out of a repr
        try:
            size = st_size_repr(self.st_size)
            mtime = mtime_repr(self.st_mtime)
        except OSError:
            size = MISSING
            mtime = MISSING
        yield 'st_size', size
        yield 'st_mtime', mtime

    def __repr__(self) -> str:
        lines = [type(self).__name__]
        lines.extend(
            f'    {name}: {bare_repr(value)}'
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
    def st_size(self) -> int:
        """Size of the entry, in bytes."""
        return self.path.stat().st_size

    @cached_property
    def st_mtime(self) -> float:
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

    def track(self) -> Self:
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
        # a path holds one entry whichever its kind
        occupant = trail.entries.path2entry.get(destination)
        if (
            occupant is not None
            and occupant is not self
        ):
            occupant.offtrail()
        collection.repath(self, previous_path)
        return self

    def offtrail(self) -> None:
        """Offtrail the entry, removing it from the collections holding it."""
        collection = self._parent
        if (
            collection is None
            or collection.id2entry.get(self.id) is not self
        ):
            return
        del collection[self.id]


class Entries[E: Entry](Collection[EntryKey, E]):
    """
    A collection of Entry objects (Asset or Dir) tracked by a Trail.

    >>> trail.entries
    Entries (4)
        0. Dir
            id: 'decbe4d041fa4c1893da693c70ad9105'
            path: '/tmp/tmpbzh09nb5/folder'
        1. Asset
            id: '41d3f259a5fc4c1fa13c516cf892f56e'
            path: '/tmp/tmpbzh09nb5/folder/new.csv'
        2. Dir
            id: 'f48807577f1d454a9caa6814af452d8e'
            path: '/tmp/tmpbzh09nb5/folder/nested'
        3. Asset
            id: '6e564e209ff44bafa32cf75d9ffcd844'
            path: '/tmp/tmpbzh09nb5/folder/nested/nested.csv'
    """
    entry_type: type[E] = Entry

    @cached_property
    def path2entry(self) -> dict[Path, E]:
        # built from data rather than beside it, so that a selection builds its own
        return {
            entry.path: entry
            for entry in self.data.values()
        }

    @property
    def id2entry(self) -> dict[str, E]:
        return self.data

    @property
    def _synced(self) -> Iterator[Self]:
        trail = self._trail
        # the trail's own assets and dirs are what `Trail.entries` is made of, so a change to
        # either is made to it as well; any other collection, a copy included, stands alone
        yield self
        if (
            self is trail.assets
            or self is trail.dirs
        ):
            yield trail.entries

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

    def __setitem__(
        self,
        key: str,
        value: E,
    ) -> None:
        if not isinstance(value, self.entry_type):
            raise TypeError(f"Expected {self.entry_type.__name__}, got {type(value).__name__}")
        if key != value.id:
            raise ValueError(f"Entry ID does not match its key: {key}")
        for collection in self._synced:
            if key not in collection.id2entry:
                collection.ids.append(key)
            collection.id2entry[key] = value
            collection.path2entry[value.path] = value

    def __delitem__(self, key: EntryKey) -> None:
        entry = self[key]
        for collection in self._synced:
            del collection.id2entry[entry.id]
            collection.ids.remove(entry.id)
            if collection.path2entry.get(entry.path) is entry:
                del collection.path2entry[entry.path]

    def repath(
        self,
        entry: E,
        previous: Path,
    ) -> None:
        for collection in self._synced:
            if collection.path2entry.get(previous) is entry:
                del collection.path2entry[previous]
        self[entry.id] = entry

    def __contains__(self, key: EntryKey | E) -> bool:
        if isinstance(key, Entry):
            return key.id in self.id2entry
        if isinstance(key, str) and key in self.id2entry:
            return True
        return Path(key).expanduser().resolve() in self.path2entry

    def clear(self) -> None:
        """
        Offtrail everything the collection holds. The removals are recorded like any other, so
        that a cleared collection stays cleared rather than coming back with the log's replay.
        """
        paths = tuple(
            entry.path
            for entry in self
        )
        if not paths:
            return
        self._trail.offtrail(*paths)

    def mark(self, *paths: PathLike) -> tuple[Entry, ...]:
        """
        Track the files the Trail's markers indicate but that it does not yet track. A file among
        `paths` is checked alone, a directory is walked, and no paths at all walks the project
        directory along with every tracked directory. Returns the entries newly tracked.
        """
        trail = self._trail
        if not trail.markers:
            return ()
        candidates: list[Path] = []
        roots: list[Path] = []
        if paths:
            for path in paths:
                path = Path(path).expanduser()
                # resolving would put the target in place of the link
                if path.is_symlink():
                    continue
                path = path.resolve()
                if path.is_dir():
                    roots.append(path)
                elif self._scoped(path):
                    candidates.append(path)
        else:
            if trail.dir is not None:
                roots.append(trail.dir.parent)
            roots.extend(
                directory.path
                for directory in trail.dirs
            )
        candidates.extend(self._walk(roots))
        marked = [
            path
            for path in dict.fromkeys(candidates)
            if self._markable(path)
        ]
        if not marked:
            return ()
        tracked = trail.track(*marked)
        if isinstance(tracked, Entry):
            return (tracked,)
        return tuple(tracked)

    def _scoped(self, path: Path) -> bool:
        # markers speak for the project directory and the tracked directories, not for whatever
        # else the watchdog observes, such as the parent of a tracked directory
        trail = self._trail
        if (
            trail.dir is not None
            and path.is_relative_to(trail.dir.parent)
        ):
            return True
        return any(
            path.is_relative_to(directory.path)
            for directory in trail.dirs
        )

    def _markable(self, path: Path) -> bool:
        trail = self._trail
        # the stat comes last, since most candidates fail on their extension alone
        return (
            trail.markers.matches(path)
            and path not in trail.entries
            and path not in trail._offtrailed_paths
            and not trail._ignored(path)
            and path.is_file()
        )

    def _walk(self, roots: Iterable[Path]) -> Iterator[Path]:
        # shallowest first, so a root nested in another is walked once, as part of its ancestor
        ordered = sorted(
            set(roots),
            key=lambda root: len(root.parts),
        )
        kept: list[Path] = []
        for root in ordered:
            if any(
                root.is_relative_to(ancestor)
                for ancestor in kept
            ):
                continue
            kept.append(root)
        for root in kept:
            for directory, dirnames, filenames in os.walk(root):
                parent = Path(directory)
                # pruned in place so that os.walk does not descend; a hidden directory is walked
                # only if it is tracked, which keeps .git and .venv out of the walk
                dirnames[:] = [
                    name
                    for name in dirnames
                    if self._descends(parent / name)
                ]
                for name in filenames:
                    path = parent / name
                    if not path.is_symlink():
                        yield path

    def _descends(self, path: Path) -> bool:
        trail = self._trail
        if (
            path.is_symlink()
            or trail._ignored(path)
            or path in trail._offtrailed_paths
        ):
            return False
        return (
            not path.name.startswith('.')
            or path in trail.dirs
        )

    def entry(self, *paths: PathLike) -> tuple[E, ...]:
        selected: dict[Path, E] = {}
        for path in paths:
            resolved = Path(path).expanduser().resolve()
            if resolved not in selected:
                selected[resolved] = (
                    self.get(resolved)
                    or self.entry_type.from_path(resolved, trail=self._trail)
                )
        tracked = []
        try:
            for entry in selected.values():
                if self.id2entry.get(entry.id) is entry:
                    entry.track()
                    continue
                while entry.id in self._trail.entries:
                    entry.id = uuid4().hex
                entry.track()
                tracked.append(entry)
        except Exception:
            for entry in reversed(tracked):
                entry.offtrail()
            raise
        return tuple(selected.values())
