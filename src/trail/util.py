from __future__ import annotations

import os
from collections import UserList
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Protocol, overload
from uuid import UUID

from trail.node import Node

# anything a path can be made from, including any object implementing __fspath__
PathLike = str | os.PathLike[str] | Path


def normalize_id(identifier: str | int) -> str:
    if isinstance(identifier, int):
        return UUID(int=identifier).hex
    return UUID(hex=identifier).hex


def list_repr(
        name: str,
        shown: Iterable[object],
        total: int,
        indent: str = '    ',
) -> list[str]:
    """Bracketed lines for a list truncated to `shown`, summarizing the remainder of `total`."""
    if not total:
        return [f'{indent}{name}: []']
    lines = [f'{indent}{name}: [']
    displayed = 0
    for value in shown:
        lines.append(f'{indent * 2}{value!r},')
        displayed += 1
    hidden = total - displayed
    if hidden > 0:
        lines.append(f'{indent * 2}<{hidden} others>')
    lines.append(f'{indent}]')
    return lines


def asset_repr(
        name: str,
        path: Path | None,
        attributes: Iterable[tuple[str, object]] | None = None,
) -> str:
    lines = [name]
    if attributes is None:
        if path is None:
            displayed_path = None
        else:
            displayed_path = str(path)
        attributes = [('path', displayed_path)]
    lines.extend(
        f'    {key}: {value!r}'
        for key, value in attributes
    )
    if path is None:
        return '\n'.join(lines)
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            offset = max(0, stream.tell() - 8192)
            stream.seek(offset)
            content = stream.read(8192)
    except FileNotFoundError:
        lines.append('    tail: <missing>')
        return '\n'.join(lines)
    except OSError as error:
        lines.append(f'    tail: <unreadable: {error.strerror}>')
        return '\n'.join(lines)

    if offset:
        newline = content.find(b'\n')
        if newline != -1 and newline < len(content) - 1:
            content = content[newline + 1:]
    tail = content.decode('utf-8', errors='replace').splitlines()[-4:]
    if not tail:
        lines.append('    tail: <empty>')
        return '\n'.join(lines)
    lines.append('    tail:')
    if offset:
        lines.append('        ...')
    for line in tail:
        if len(line) > 240:
            line = line[:237] + '...'
        lines.append(f'        {line}')
    return '\n'.join(lines)


class Repr(Protocol):
    def _repr_items(self) -> Iterator[tuple[str, object]]: ...


def items_repr(
        name: str,
        items: Iterable[Repr],
) -> str:
    """Multi-line repr for a collection, expanding each item's fields onto its own line."""
    items = list(items)
    lines = [f'{name} ({len(items)})']
    for position, item in enumerate(items):
        lines.append(f'    {position}. {type(item).__name__}')
        lines.extend(
            f'        {key}: {value!r}'
            for key, value in item._repr_items()
        )
    return '\n'.join(lines)


class Listing[T: Repr](UserList[T]):
    """
    The list yielded by slicing a `ByPos`; reprs like the collection it was sliced from,
    rather than as a single dense line per item.
    """

    def __init__(
            self,
            initlist: Iterable[T] | None = None,
            /,
            name: str | None = None,
    ) -> None:
        UserList.__init__(self, initlist)
        if name is None:
            name = type(self).__name__
        self.name = name

    @overload
    def __getitem__(self, item: int) -> T: ...

    @overload
    def __getitem__(self, item: slice) -> Listing[T]: ...

    def __getitem__(self, item: int | slice) -> T | Listing[T]:
        if isinstance(item, slice):
            return type(self)(self.data[item], name=self.name)
        return self.data[item]

    def __repr__(self) -> str:
        return items_repr(self.name, self.data)


class Positioned[T](Protocol):
    @property
    def ids(self) -> list[str]: ...

    def __getitem__(self, key: str) -> T: ...


class ByPos[T](Node):
    _parent: Positioned[T]

    def __init__(self, parent: Positioned[T]) -> None:
        Node.__init__(self, parent)
        self._parent = parent

    @overload
    def __getitem__(self, item: int) -> T: ...

    @overload
    def __getitem__(self, item: slice) -> Listing[T]: ...

    def __getitem__(self, item: int | slice) -> T | Listing[T]:
        collection = self._parent
        if isinstance(item, slice):
            selected = [
                collection[identifier]
                for identifier in collection.ids[item]
            ]
            name = getattr(collection, '_repr_name', type(collection).__name__)
            return Listing(selected, name=name)
        identifier = collection.ids[item]
        return collection[identifier]

    def __len__(self) -> int:
        return len(self._parent.ids)
