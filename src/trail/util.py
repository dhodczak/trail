from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol
from uuid import UUID

# anything a path can be made from, including any object implementing __fspath__
PathLike = str | os.PathLike[str] | Path
# the units a byte count is worded in, each 1024 of the one before it
SIZE_UNITS: Final[tuple[str, ...]] = ('KiB', 'MiB', 'GiB', 'TiB')
# what a stat reports for a resource that has gone since it was tracked
MISSING: Final = '<missing>'


def normalize_id(identifier: str | int) -> str:
    if isinstance(identifier, int):
        return UUID(int=identifier).hex
    return UUID(hex=identifier).hex


def st_size_repr(size: int) -> str:
    """
    A byte count with its unit. Under a kibibyte it is exact, above it rounded to one decimal
    place, which is the precision a reader comparing two sizes by eye can use.
    """
    if size < 1024:
        return f'{size} B'
    scaled = float(size)
    for unit in SIZE_UNITS[:-1]:
        scaled /= 1024
        # promoted on the rounded value, so a size just under a threshold reads '1.0 MiB'
        # rather than '1024.0 KiB'
        if round(scaled, 1) < 1024:
            return f'{scaled:.1f} {unit}'
    return f'{scaled / 1024:.1f} {SIZE_UNITS[-1]}'


def mtime_repr(mtime: float) -> str:
    """
    An mtime as a local wall clock. The stored value is seconds since the epoch, which no
    reader converts in their head, so the repr states the moment instead of the unit.
    """
    moment = datetime.fromtimestamp(mtime, UTC).astimezone()
    return f'{moment:%Y-%m-%d %H:%M:%S}'


def bare_repr(value: object) -> str:
    """
    A field as a reader wants to paste it: a string as itself, anything else as it reprs. The
    quotes go back on only when they are what keeps the line legible, which is when the string
    is empty, when whitespace at either end of it would otherwise be invisible, or when a
    character in it would not survive being printed into the feed.
    """
    if not isinstance(value, str):
        return repr(value)
    if not value or value != value.strip() or not value.isprintable():
        return repr(value)
    return value


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
        lines.append(f'{indent * 2}{bare_repr(value)},')
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
        f'    {key}: {bare_repr(value)}'
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
            f'        {key}: {bare_repr(value)}'
            for key, value in item._repr_items()
        )
    return '\n'.join(lines)
