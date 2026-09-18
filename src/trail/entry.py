from __future__ import annotations

from collections.abc import ItemsView, Iterable, Iterator
from dataclasses import dataclass, field, fields
from functools import cached_property
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import TYPE_CHECKING, Self, overload
from uuid import uuid4

from trail.node import Node
from trail.util import ByPos, normalize_id

if TYPE_CHECKING:
    from trail.event import Event
    from trail.trail import Trail

EntryKey = str | Path


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
        path: str | Path,
        trail: Trail | None = None,
    ) -> Self:
        from trail.dir import Dir
        from trail.file import File

        path = Path(path).expanduser().resolve()
        metadata = path.stat()

        if trail is not None and trail._ignored(path):
            raise ValueError(f"Cannot track Trail metadata: {path}")

        if issubclass(cls, Dir):
            if not S_ISDIR(metadata.st_mode):
                raise ValueError(f"Not a directory: {path}")
        elif issubclass(cls, File):
            if not S_ISREG(metadata.st_mode):
                raise ValueError(f"Not a regular file: {path}")
        else:
            if S_ISDIR(metadata.st_mode):
                cls = Dir
            elif S_ISREG(metadata.st_mode):
                cls = File
            else:
                raise ValueError(f"Not a regular file or directory: {path}")

        out = cls(path=path)
        out._trail = trail
        if trail is not None:
            if isinstance(out, Dir):
                out._parent = trail.dirs
            else:
                out._parent = trail.files
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

    def add(self) -> Self:
        raise NotImplementedError

    def remove(self) -> None:
        """Remove the entry from the tracked Entries collection."""
        collection = self._parent
        if collection is None or collection.id2entry.get(self.id) is not self:
            return
        del collection.id2entry[self.id]
        collection.ids.remove(self.id)
        if collection.path2entry.get(self.path) is self:
            del collection.path2entry[self.path]


class Entries[E: Entry](Node):
    """
    A collection of Entry objects (File or Dir) tracked by a Trail.

    >>> trail.entries
    Entries (4)
        0. File
            id: '41d3f259a5fc4c1fa13c516cf892f56e'
            path: '/tmp/tmpbzh09nb5/folder/new.csv'
        1. File
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
        lines = [f'{type(self).__name__} ({len(self)})']
        for position, identifier in enumerate(self.ids):
            entry = self[identifier]
            lines.append(f'    {position}. {type(entry).__name__}')
            lines.extend(
                f'        {name}: {value!r}'
                for name, value in entry._repr_items()
            )
        return '\n'.join(lines)

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
        if isinstance(key, (str, Path)):
            return self.path2entry[Path(key).expanduser().resolve()]
        selected = []
        for value in key:
            if not isinstance(value, (str, Path)):
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

    def add(self, *paths: str | Path) -> tuple[E, ...]:
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
                    entry.add()
                    continue
                while (
                    entry.id in self._trail.files
                    or entry.id in self._trail.dirs
                ):
                    entry.id = uuid4().hex
                entry.add()
                registered.append(entry)
        except Exception:
            for entry in reversed(registered):
                entry.remove()
            raise
        return tuple(selected.values())
