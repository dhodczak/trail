from __future__ import annotations

from collections.abc import Iterable, Iterator, MutableSet
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Final

from trail.node import Node
from trail.util import PathLike, asset_repr

if TYPE_CHECKING:
    from trail.trail import Trail

# path separators and wildcards cannot be part of an extension, and '!' and '#' are held back
# for the gitignore syntax the file may grow into
RESERVED: Final[frozenset[str]] = frozenset('/\\*?[]!#')


class Text(Node):
    """
    The `.markers` file beside the trail directory. Markers are added and removed by editing
    their own lines in place, so the comments and blank lines a user writes into it survive.
    """

    _parent: Markers

    def __repr__(self) -> str:
        return asset_repr(type(self).__name__, self.path)

    @cached_property
    def path(self) -> Path | None:
        path = self._trail.dir
        if path:
            return path.parent / '.markers'
        return None

    def _lines(self) -> list[str]:
        path = self.path
        if (
            path is None
            or not path.exists()
        ):
            return []
        return path.read_text(encoding='utf-8').splitlines()

    @staticmethod
    def _marker(line: str) -> str | None:
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith('#')
        ):
            return None
        return Markers.normalize(stripped)

    def read(self) -> None:
        loaded = set()
        for number, line in enumerate(self._lines(), start=1):
            try:
                marker = self._marker(line)
            except ValueError as error:
                msg = f'{self.path}:{number}: {error}'
                raise ValueError(msg) from None
            if marker is not None:
                loaded.add(marker)
        data = self._parent.data
        data.clear()
        data.update(loaded)

    def write(self, text: str) -> None:
        path = self.path
        if path is None:
            return
        # written aside and swapped in, so that a reader never sees a half-written file
        staged = path.with_name(f'{path.name}.tmp')
        staged.write_text(text, encoding='utf-8')
        staged.replace(path)

    def append(self, *markers: str) -> None:
        if not markers:
            return
        lines = self._lines()
        lines.extend(markers)
        text = ''.join(
            f'{line}\n'
            for line in lines
        )
        self.write(text)

    def remove(self, *markers: str) -> None:
        if not markers:
            return
        removed = set(markers)
        # every line spelling a removed marker goes, '.CSV' along with 'csv'
        kept = [
            line
            for line in self._lines()
            if self._marker(line) not in removed
        ]
        text = ''.join(
            f'{line}\n'
            for line in kept
        )
        self.write(text)

    def clear(self) -> None:
        # every marker line goes, and the comments and blank lines around them stay
        kept = [
            line
            for line in self._lines()
            if self._marker(line) is None
        ]
        text = ''.join(
            f'{line}\n'
            for line in kept
        )
        self.write(text)


class Markers(
    Node,
    MutableSet[str],
):
    """
    The extensions a Trail follows of its own accord, held lowercase and without their leading
    dot. Every change is written through to the `.markers` file, which is the record of them.
    """

    _parent: Trail

    def __init__(self, parent: Trail | None = None) -> None:
        Node.__init__(self, parent)
        self.data: set[str] = set()

    @cached_property
    def text(self) -> Text:
        return Text(self)

    @staticmethod
    def normalize(marker: str) -> str:
        normalized = (
            marker
            .strip()
            .removeprefix('.')
            .lower()
        )
        if (
            not normalized
            or normalized.startswith('.')
            or normalized.endswith('.')
            or any(
                char.isspace()
                or char in RESERVED
                for char in normalized
            )
        ):
            msg = f'Not an extension: {marker!r}'
            raise ValueError(msg)
        return normalized

    @classmethod
    def _from_iterable(cls, iterable: Iterable[str]) -> set[str]:
        # the set operators build their result through here; `markers | {'tif'}` is a value,
        # not a second Markers writing to the same file
        return set(iterable)

    def __contains__(self, value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            marker = self.normalize(value)
        except ValueError:
            return False
        return marker in self.data

    def __iter__(self) -> Iterator[str]:
        # sorted, since a set of strings iterates in an order that changes with every process
        return iter(sorted(self.data))

    def __len__(self) -> int:
        return len(self.data)

    def add(self, value: str) -> None:
        marker = self.normalize(value)
        if marker in self.data:
            return
        self.data.add(marker)
        self.text.append(marker)

    def discard(self, value: str) -> None:
        try:
            marker = self.normalize(value)
        except ValueError:
            return
        if marker not in self.data:
            return
        self.data.remove(marker)
        self.text.remove(marker)

    def clear(self) -> None:
        # one rewrite of the file, rather than the one per marker that MutableSet.clear makes
        self.data.clear()
        self.text.clear()

    def matches(self, path: PathLike) -> bool:
        suffixes = Path(path).suffixes
        # every trailing run of suffixes, so both 'gz' and 'tar.gz' mark 'a.tar.gz'
        for i in range(len(suffixes)):
            extension = (
                ''
                .join(suffixes[i:])
                .removeprefix('.')
                .lower()
            )
            if extension in self.data:
                return True
        return False

    def __repr__(self) -> str:
        lines = [f'{type(self).__name__} ({len(self)})']
        lines.extend(
            f'    {marker}'
            for marker in self
        )
        return '\n'.join(lines)
