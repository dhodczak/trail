from __future__ import annotations

import json
import os
import shutil
import tempfile
import weakref
from collections.abc import Iterable, Iterator
from functools import cached_property
from itertools import chain, count
from pathlib import Path
from typing import Self

# NOTE: AI Generated

class Node:
    """

    A member of a playground tree. A node holds only its parent and its own filename, so the path
    falls out of the chain and an ancestor which moves repaths everything beneath it.
    """

    suffix: str = ''

    def __init__(
        self,
        parent: Dir | None = None,
        stem: str = '',
        suffix: str | None = None,
    ) -> None:
        self._parent = parent
        self.stem = stem
        if suffix is not None:
            self.suffix = suffix

    @property
    def _playground(self) -> Playground:
        parent = self._parent
        if isinstance(parent, Playground):
            return parent
        return parent._playground

    @cached_property
    def path(self) -> Path:
        return self._parent.path / f'{self.stem}{self.suffix}'

    @property
    def filename(self) -> str:
        """The current name on disk, which a move may have changed."""
        return self.path.name

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @cached_property
    def _numbers(self) -> Iterator[int]:
        """generated payloads count up, so repeated appends stay distinguishable"""
        return count(1)

    @cached_property
    def _adhoc(self) -> list[Node]:
        """members addressed by filename rather than declared as attributes"""
        return []

    def children(self) -> Iterator[Node]:
        """
        The members which have been reached for; the rest derive their path from this node when
        they are first accessed, so only these can be left holding a stale one.
        """
        adhoc = self._adhoc
        values = chain(tuple(self.__dict__.values()), adhoc)
        for value in values:
            if (
                isinstance(value, Node)
                and value._parent is self
            ):
                yield value

    def create(self) -> Self:
        raise NotImplementedError

    def remove(self) -> Self:
        raise NotImplementedError

    def move(self, destination: str | Path | Node | None = None) -> Self:
        """
        Rename the resource and follow it, so this node and the members beneath it keep pointing
        at the moved tree. Without a destination the node takes its next numbered sibling name;
        a directory destination keeps the current filename.
        """
        self.create()
        previous = self.path
        current = self._destination(destination)
        current.parent.mkdir(parents=True, exist_ok=True)
        previous.rename(current)
        self.path = current
        for child in self.children():
            child._repath(previous, current)
        return self

    def _destination(self, destination: str | Path | Node | None) -> Path:
        if destination is None:
            return self._numbered()
        if isinstance(destination, Dir):
            destination.create()
            return destination.path / self.filename
        if isinstance(destination, Node):
            return destination.path
        out = Path(destination).expanduser()
        if not out.is_absolute():
            out = self.path.parent / out
        if out.is_dir():
            out /= self.filename
        return out

    def _numbered(self) -> Path:
        """the next free `<stem>-<number>` sibling"""
        number = 1
        while True:
            candidate = self.path.with_name(f'{self.stem}-{number}{self.suffix}')
            if not candidate.exists():
                return candidate
            number += 1

    def _repath(
        self,
        previous: Path,
        current: Path,
    ) -> None:
        """follow an ancestor which moved, preserving whatever rename this node has"""
        if not self.path.is_relative_to(previous):
            return
        self.path = current / self.path.relative_to(previous)
        for child in self.children():
            child._repath(previous, current)

    def __fspath__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        # deliberately stats rather than reads: a repr evaluated in a debugger or a test must not
        # emit an `opened` event of its own
        lines = [
            type(self).__name__,
            f'    path: {str(self.path)!r}',
            f'    exists: {self.exists!r}',
        ]
        return '\n'.join(lines)


class Asset(Node):
    """A file in the playground tree."""

    @property
    def content(self) -> str:
        """the payload written when a missing asset is created"""
        return f'{self._line()}\n'

    def _line(self) -> str:
        return f'line {next(self._numbers)}'

    def create(self, content: str | None = None) -> Self:
        """Write the default payload unless the asset is already there."""
        if self.exists:
            return self
        if content is None:
            content = self.content
        return self.write(content)

    def write(self, content: str) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content, encoding='utf-8')
        return self

    def open(self) -> str:
        """
        Read the asset, which is what produces an `opened` event, and return its text. A missing
        asset is created first so a test can go straight to the event it wants.
        """
        self.create()
        return self.path.read_text(encoding='utf-8')

    def append(self, *lines: str) -> Self:
        """Append lines, defaulting to one generated line."""
        self.create()
        if not lines:
            lines = (self._line(),)
        with self.path.open('a', encoding='utf-8') as stream:
            for line in lines:
                stream.write(f'{line}\n')
        return self

    def remove(self) -> Self:
        self.path.unlink(missing_ok=True)
        return self

    def __repr__(self) -> str:
        lines = [super().__repr__()]
        if self.exists:
            lines.append(f'    size: {self.path.stat().st_size!r}')
        return '\n'.join(lines)


class CSV(Asset):
    suffix = '.csv'
    header = 'name,value'

    @property
    def content(self) -> str:
        return f'{self.header}\n{self.row()}\n'

    def row(self) -> str:
        number = next(self._numbers)
        return f'row{number},{number}'

    def append(self, *rows: str | Iterable[object]) -> Self:
        """Append rows, defaulting to one generated row; a non-string row joins on commas."""
        self.create()
        if not rows:
            rows = (self.row(),)
        rendered = [
            self._render(row)
            for row in rows
        ]
        return super().append(*rendered)

    @staticmethod
    def _render(row: str | Iterable[object]) -> str:
        if isinstance(row, str):
            return row
        return ','.join(
            str(value)
            for value in row
        )


class GeoJSON(Asset):
    suffix = '.geojson'

    @property
    def content(self) -> str:
        return self._dump([self.feature()])

    def feature(self, **properties) -> dict:
        """a point feature whose coordinates and name count up with each call"""
        number = next(self._numbers)
        out = {
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [float(number), float(number)],
            },
            'properties': {'name': f'point{number}'} | properties,
        }
        return out

    def features(self) -> list[dict]:
        """the features on disk; reading them produces an `opened` event"""
        return json.loads(self.open())['features']

    def append(self, *features: dict) -> Self:
        """
        Rewrite the collection with `features` added, defaulting to one generated point.
        A GeoJSON append is a read-modify-write, so the asset is opened before it is modified.
        """
        collection = self.features()
        if not features:
            features = (self.feature(),)
        collection.extend(features)
        return self.write(self._dump(collection))

    @staticmethod
    def _dump(features: list[dict]) -> str:
        collection = {
            'type': 'FeatureCollection',
            'features': features,
        }
        return f'{json.dumps(collection, indent=2)}\n'


class Dir(Node):
    """A directory in the playground tree, holding the same members as the playground itself."""

    # paths listed by __repr__ before the remainder is summarized
    repr_limit = 20

    @cached_property
    def csv(self) -> CSV:
        return CSV(self, 'csv')

    @cached_property
    def geojson(self) -> GeoJSON:
        return GeoJSON(self, 'geojson')

    @cached_property
    def dir(self) -> Dir:
        return Dir(self, 'dir')

    def asset(self, filename: str) -> Asset:
        return self._child(filename, self._asset_type(filename))

    def subdir(self, filename: str) -> Dir:
        return self._child(filename, Dir)

    def _child[N: Node](
        self,
        filename: str,
        node_type: type[N],
    ) -> N:
        """
        An ad-hoc member addressed by filename rather than declared as an attribute. Whichever
        member already points at that path wins, so a declared `csv` and an `asset('csv.csv')`
        are the same node.
        """
        path = self.path / filename
        for child in self.children():
            if (
                child.path == path
                and isinstance(child, node_type)
            ):
                return child
        node = node_type(self, Path(filename).stem, Path(filename).suffix)
        self._adhoc.append(node)
        return node

    @staticmethod
    def _asset_type(filename: str) -> type[Asset]:
        types = {
            CSV.suffix: CSV,
            GeoJSON.suffix: GeoJSON,
        }
        return types.get(Path(filename).suffix, Asset)

    def create(self) -> Self:
        self.path.mkdir(parents=True, exist_ok=True)
        return self

    def remove(self) -> Self:
        shutil.rmtree(self.path, ignore_errors=True)
        return self

    def __repr__(self) -> str:
        lines = [
            type(self).__name__,
            f'    path: {str(self.path)!r}',
        ]
        lines.extend(self._tree())
        return '\n'.join(lines)

    def _tree(self) -> list[str]:
        """the tree as it is on disk, which is not the same as the members which exist as nodes"""
        if not self.exists:
            return ['    tree: <missing>']
        paths = sorted(self.path.rglob('*'))
        if not paths:
            return ['    tree: []']
        lines = ['    tree: [']
        for path in paths[:self.repr_limit]:
            relative = str(path.relative_to(self.path))
            if path.is_dir():
                relative = f'{relative}/'
            lines.append(f'        {relative!r},')
        hidden = len(paths) - self.repr_limit
        if hidden > 0:
            lines.append(f'        <{hidden} others>')
        lines.append('    ]')
        return lines


class Playground(Dir):
    """
    AI Generated

    A disposable filesystem tree for exercising a Trail without hand-rolling temporary
    directories, files, appends and renames in every test. The playground is the root directory,
    so it carries the same members as any directory beneath it.

    Members are lazy: a node is only a name and a parent until something is created, opened,
    appended, moved or removed, so a test names the resource it wants and then triggers only the
    events it cares about.

        playground = Playground()
        trail = Trail(playground.path)
        playground.csv.create()
        trail.register(playground.csv)
        playground.csv.open()
        playground.dir.geojson.append()
        playground.dir.move()

    `playground.csv` is `<root>/csv.csv`, `playground.dir.geojson` is `<root>/dir/geojson.geojson`,
    and `dir` nests without limit. Nodes are os.PathLike, so one can be handed to anything which
    takes a path. A playground which made its own directory sweeps it up when it is collected or
    when the process exits; one given a directory leaves it alone until it is asked to remove it.

    >>> playground
    Playground
        path: '/dev/shm/playground-_6oh8mig'
        tree: [
            'csv.csv',
            'dir/',
            'dir/geojson.geojson',
        ]
    """

    def __init__(
        self,
        dir: str | Path | None = None,
        cleanup: bool | None = None,
    ) -> None:
        if cleanup is None:
            # a directory handed to the playground is not the playground's to delete unasked
            cleanup = dir is None
        if dir is None:
            shared = Path('/dev/shm')
            if os.access(shared, os.W_OK):
                tempdir = shared
            else:
                tempdir = Path(tempfile.gettempdir())
            dir = tempfile.mkdtemp(prefix='playground-', dir=tempdir)
        path = Path(dir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        super().__init__(stem=path.name)
        self.path = path
        self._cleanup = cleanup
        self._finalizer = None
        self._arm()

    @property
    def _playground(self) -> Playground:
        return self

    def _arm(self) -> None:
        """aim the sweep at the current path, so a playground which moves still cleans up"""
        if self._finalizer is not None:
            self._finalizer.detach()
        if self._cleanup:
            self._finalizer = weakref.finalize(self, shutil.rmtree, self.path, True)

    def move(self, destination: str | Path | Node | None = None) -> Self:
        super().move(destination)
        self._arm()
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exception) -> None:
        self.remove()


if __name__ == '__main__':
    with Playground() as playground:
        print(f'playground: {playground.path}')

        # a member is only a path until it is asked to exist
        print(f'csv:        {playground.csv.path} (exists={playground.csv.exists})')
        playground.csv.create()
        playground.csv.append()
        playground.csv.append('explicit,7')
        print(playground.csv.open(), end='')

        # appending to a GeoJSON rewrites the collection rather than the file's last line
        playground.geojson.append()
        names = [
            feature['properties']['name']
            for feature in playground.geojson.features()
        ]
        print(f'features:   {names}')

        # members nest, and a directory which moves takes the members beneath it along
        playground.dir.csv.open()
        playground.dir.geojson.create()
        playground.dir.move()
        print(f'moved dir:  {playground.dir.path}')
        print(f'moved csv:  {playground.dir.csv.path}')

        # a directory as the destination means "move into", and it is made on demand
        playground.csv.move(playground.subdir('archive'))
        print(f'moved into: {playground.csv.path}')
        print(playground)

    print(f'swept up:   {not playground.exists}')
