from __future__ import annotations

import asyncio
import json
from collections import UserDict
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from trail.entry import Entry
from trail.node import Node
from trail.util import ByPos, asset_repr, items_repr, mtime_repr, normalize_id, st_size_repr

if TYPE_CHECKING:
    from trail.trail import Trail


@dataclass(kw_only=True, slots=True, repr=False)
class Event:
    classes: ClassVar[dict[str, type[Event]]] = {}

    entry: Entry | None = field(default=None, init=False, repr=False)
    id: str = field(default_factory=lambda: uuid4().hex, repr=True)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.id = normalize_id(self.id)

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        for event_field in fields(self):
            if not event_field.repr:
                continue
            value = getattr(self, event_field.name)
            if event_field.name == 'timestamp':
                now = datetime.now(value.tzinfo)
                date_prefix = ''
                if value.year != now.year:
                    date_prefix = value.strftime('%Y-%m-%d ')
                elif value.month != now.month:
                    date_prefix = value.strftime('%m-%d ')
                elif value.day != now.day:
                    date_prefix = value.strftime('%d ')
                value = (
                    f'{date_prefix}{value:%H:%M:%S}.'
                    f'{value.microsecond // 1000:03d}'
                )
            if value is None or value == '':
                continue
            # the baseline is stored as the stat reported it; the units are put back here
            if event_field.name == 'st_size':
                value = st_size_repr(value)
            elif event_field.name == 'st_mtime':
                value = mtime_repr(value)
            yield event_field.name, value

    def __repr__(self) -> str:
        attributes = ', '.join(
            f'{name}={value}'
            for name, value in self._repr_items()
        )
        return f'{type(self).__name__}({attributes})'

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Entry | None:
        raise NotImplementedError

    def __init_subclass__(cls, **kwargs) -> None:
        # slots=True replaces the class captured by zero-argument super()
        super(Event, cls).__init_subclass__(**kwargs)
        cls.classes[cls.__name__] = cls

    @classmethod
    def from_record(
        cls, /, trail: Trail, resolved_entry: Entry | None = None, **record
    ) -> Event:
        name = record.pop("cls")
        event_cls = cls.classes.get(name)

        if name == cls.__name__:
            event_cls = cls
        if event_cls is None:
            raise ValueError(f"Unknown event class: {name!r}")
        entry_id = normalize_id(record.pop("entry"))
        record["timestamp"] = datetime.fromisoformat(record["timestamp"])
        deferred = {
            field.name: record.pop(field.name)
            for field in fields(event_cls)
            if not field.init and field.name in record
        }
        event = event_cls(**record)
        for name, value in deferred.items():
            setattr(event, name, value)

        if resolved_entry is None:
            resolved_entry = trail.entries[entry_id]
        elif resolved_entry.id != entry_id:
            raise ValueError(f"Entry ID does not match event record: {entry_id}")
        event.entry = resolved_entry

        return event

    def to_record(self) -> dict:
        out = {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "entry"
        }
        out["cls"] = type(self).__name__
        out["timestamp"] = self.timestamp.isoformat()
        out["entry"] = self.entry.id
        return out


@dataclass(kw_only=True, slots=True, repr=False)
class AddEntryEvent(Event):
    """
    A resource being tracked. The recorded size and mtime are the baseline the filesystem is
    later compared against, so they are sampled once here and never refreshed on replay.
    """

    src_path: str
    st_size: int | None = field(default=None, init=False)
    st_mtime: float | None = field(default=None, init=False)
    is_directory: bool = field(default=False, init=False, repr=False)
    entry: Entry | None = field(default=None, init=False, repr=False)

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Entry:
        if replay:
            entry = self.entry
        else:
            entry = trail.entries.get(self.src_path)
        if entry is None:
            entry = Entry.from_path(self.src_path, trail=trail)
        self.entry = entry

        entry.track()
        self.is_directory = entry.path in trail.dirs
        if not replay:
            self._stat(entry)
        trail._offtrailed_paths.discard(entry.path)
        return entry

    def _stat(self, entry: Entry) -> None:
        # a resource may vanish between resolution and being tracked; an unrecorded
        # baseline is preferable to refusing to track it outright
        try:
            metadata = entry.path.stat()
        except OSError:
            return
        self.st_size = metadata.st_size
        self.st_mtime = metadata.st_mtime


@dataclass(kw_only=True, slots=True, repr=False)
class RemoveEntryEvent(Event):
    src_path: str
    entry: Entry | None = field(default=None, init=False, repr=False)

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Entry | None:
        if replay:
            entry = self.entry
        else:
            entry = trail.entries.get(self.src_path)
        if entry is None:
            return None
        self.entry = entry
        entry.offtrail()
        trail._offtrailed_paths.add(entry.path)
        return entry


@dataclass(kw_only=True, slots=True, repr=False)
class WatchdogEvent(Event):
    src_path: str
    dest_path: str = ""
    event_type: str = ""
    is_directory: bool = field(default=False, repr=False)
    is_synthetic: bool = field(default=False, repr=False)
    entry: Entry | None = field(default=None, init=False, repr=False)

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Entry | None:
        if replay:
            entry = self.entry
            if entry is None:
                raise ValueError("Cannot replay a watchdog event without an entry")
            if self.event_type == "moved" and self.dest_path:
                entry.move(self.dest_path)
            elif self.event_type == "created":
                entry.track()
            return entry

        if self.event_type not in {
            "created",
            "modified",
            "deleted",
            "moved",
            "opened",
        } or (self.is_directory and self.event_type == "modified"):
            return None
        source = Path(self.src_path).expanduser().resolve()
        destination = None
        if self.event_type == "moved" and self.dest_path:
            destination = Path(self.dest_path).expanduser().resolve()
        for path in (source, destination):
            if path is not None and (
                path in trail._offtrailed_paths or trail._ignored(path)
            ):
                return None
        if self.is_directory:
            collection = trail.dirs
        else:
            collection = trail.assets
        entry = collection.get(source)
        if entry is None and destination is not None:
            entry = collection.get(destination)
        if entry is None:
            if destination is None:
                path = source
            else:
                path = destination
            target = Path(self.dest_path or self.src_path)
            if (
                self.event_type not in {"created", "moved"}
                or trail.dirs.get(path.parent) is None
                or target.is_symlink()
            ):
                return None
            try:
                if self.is_directory:
                    exists = path.is_dir()
                else:
                    exists = path.is_file()
                if not exists:
                    return None
                entry = collection.entry(path)[0]
            except (FileNotFoundError, NotADirectoryError):
                return None
        self.src_path = str(source)
        if destination is not None:
            self.dest_path = str(destination)
            if entry.path != destination:
                entry.move(destination)
        elif self.event_type == "created":
            entry.track()
        if self.is_directory and self.event_type == "deleted":
            trail.watchdog.invalidate(source)
        self.entry = entry
        trail.events[self.id] = self
        return entry


@dataclass(kw_only=True, slots=True, repr=False)
class JupyterEvent(Event):
    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Entry | None:
        return self.entry


class JSONL(Node):
    _parent: Events

    def __repr__(self) -> str:
        return asset_repr(type(self).__name__, self.path)

    @property
    def path(self) -> Path | None:
        trail = self._trail
        if trail.dir:
            return trail.dir / "events.jsonl"
        else:
            return None

    def read(self) -> None:
        from trail.asset import Asset
        from trail.dir import Dir

        path = self.path
        trail = self._trail
        if path is None or not path.exists():
            return
        loaded: dict[str, Event] = {}
        entries: dict[str, Entry] = {}
        with path.open(encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                record = json.loads(line)
                entry_id = normalize_id(record["entry"])
                entry = entries.get(entry_id)
                if entry is None:
                    entry = trail.entries.get(entry_id)
                    if entry is None:
                        entry_path = Path(record["src_path"]).expanduser().resolve()
                        if record.get("is_directory", entry_path.is_dir()):
                            entry = Dir(path=entry_path, id=entry_id)
                            entry._parent = trail.dirs
                        else:
                            entry = Asset(path=entry_path, id=entry_id)
                            entry._parent = trail.assets
                    entries[entry_id] = entry
                event = Event.from_record(trail=trail, resolved_entry=entry, **record)
                if event.id in loaded:
                    raise ValueError(f"Duplicate event ID in {path}: {event.id}")
                loaded[event.id] = event

                event.apply(trail, replay=True)
        self._parent.data.clear()
        self._parent.data.update(loaded)
        self._parent.ids[:] = loaded

    def write(self) -> None:
        path = self.path
        if path is None:
            return
        text = "".join(
            json.dumps(event.to_record(), ensure_ascii=False) + "\n"
            for event in self._parent.values()
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def append(self, events: Iterable[Event]) -> None:
        path = self.path
        if path is None:
            return
        text = "".join(
            json.dumps(event.to_record(), ensure_ascii=False) + "\n" for event in events
        )
        if not text:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as file:
            if file.tell():
                file.seek(-1, 2)
                if file.read(1) != b"\n":
                    file.write(b"\n")
            file.write(text.encode("utf-8"))


class Watch(Node):
    """
    Suspends a consumer until the Trail records something, rather than leaving it to poll the
    collection. Each iteration holds its own waiter and its own cursor into the log, so several
    consumers may watch at once without consuming one another's events.
    """

    _parent: Events

    @cached_property
    def waiters(self) -> set[asyncio.Event]:
        """The flag held by each live iteration, raised when the log is appended to."""
        return set()

    def notify(self) -> None:
        """Wake every iteration so it may drain whatever was just appended."""
        for waiter in self.waiters:
            waiter.set()

    def __call__(
            self,
            timeout: float | None = None,
    ) -> AsyncIterator[Event]:
        """
        Yield each event appended after this call, suspending until the next one arrives rather
        than polling the collection. The cursor is taken here rather than when iteration begins,
        so a watcher made before the action which triggers the event cannot miss it, however long
        the loop takes to reach the `async for`. Iteration ends once `timeout` seconds have
        elapsed, so a `for ... else` distinguishes the awaited event from the deadline.

            watching = trail.events.watch(timeout=5)
            asset.open()
            async for event in watching:
                if isinstance(event, WatchdogEvent):
                    break
            else:
                raise TimeoutError
        """
        return self._iterate(len(self._parent.ids), timeout)

    async def _iterate(
            self,
            position: int,
            timeout: float | None,
    ) -> AsyncIterator[Event]:
        events = self._parent
        loop = asyncio.get_running_loop()
        if timeout is None:
            deadline = None
        else:
            deadline = loop.time() + timeout
        waiter = asyncio.Event()
        self.waiters.add(waiter)
        try:
            while True:
                # cleared before the backlog is drained, so an arrival during a yield is not lost
                waiter.clear()
                # events removed while watching would leave the position beyond the collection
                position = min(position, len(events.ids))
                while position < len(events.ids):
                    event = events.data[events.ids[position]]
                    position += 1
                    yield event
                if deadline is None:
                    remaining = None
                else:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        return
                try:
                    await asyncio.wait_for(waiter.wait(), remaining)
                except TimeoutError:
                    return
        finally:
            self.waiters.discard(waiter)

    def __repr__(self) -> str:
        lines = [
            type(self).__name__,
            f'    waiters: {len(self.waiters)!r}',
        ]
        return '\n'.join(lines)


class Events(
    UserDict[str, Event],
    Node,
):
    """
    A collection of Event objects that have occurred in a Trail.

    >>> trail.events
    Events (9)
        0. AddEntryEvent
            id: '8a43da699bf242e7976e7bb74df81d0f'
            timestamp: '06:02:12.114'
            src_path: '/tmp/tmpbzh09nb5/folder'
        1. WatchdogEvent
            id: 'c2152a8b8f31416abbfe7106fce8cb6c'
            timestamp: '06:02:12.130'
            src_path: '/tmp/tmpbzh09nb5/folder/new.csv'
            event_type: 'created'
        2. WatchdogEvent
            id: '620c0779453a4e5db975fa11e9efac7b'
            timestamp: '06:02:12.130'
            src_path: '/tmp/tmpbzh09nb5/folder/new.csv'
            event_type: 'opened'
    """

    _parent: Trail

    def __init__(self, parent: Trail) -> None:
        UserDict.__init__(self)
        Node.__init__(self, parent)
        self.ids: list[str] = []

    @cached_property
    def jsonl(self) -> JSONL:
        return JSONL(self)

    @cached_property
    def watch(self) -> Watch:
        """Awaits the events yet to be recorded, in place of polling the collection."""
        return Watch(self)

    @cached_property
    def by_pos(self) -> ByPos[Event]:
        """Allows for Events to be indexed by integer position, rather than ID or path."""
        return ByPos(self)

    def update(
            self,
            m: Mapping[str, Event] | Iterable[tuple[str, Event]],
            /,
    ) -> None:
        batch = dict(m)
        for value in batch.values():
            if not isinstance(value, Event):
                raise TypeError(f"Expected Event, got {type(value).__name__}")
        replacing = any(
            key in self.data
            for key in batch
        )
        if not replacing:
            self.jsonl.append(batch.values())
        appended = [
            key
            for key in batch
            if key not in self.data
        ]
        self.ids.extend(appended)
        self.data.update(batch)
        if replacing:
            self.jsonl.write()
        if appended:
            self.watch.notify()

    def __setitem__(
        self,
        key: str,
        value: Event,
    ) -> None:
        if not isinstance(value, Event):
            raise TypeError(f"Expected Event, got {type(value).__name__}")
        if key in self.data:
            self.data[key] = value
            self.jsonl.write()
        else:
            self.jsonl.append([value])
            self.data[key] = value
            self.ids.append(key)
            self.watch.notify()

    def __delitem__(self, key: str) -> None:
        del self.data[key]
        self.ids.remove(key)
        self.jsonl.write()

    def clear(self) -> None:
        self.data.clear()
        self.ids.clear()
        self.jsonl.write()

    def __repr__(self) -> str:
        return items_repr(type(self).__name__, self.data.values())
