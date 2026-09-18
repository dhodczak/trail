from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from functools import cached_property
from os import fsdecode
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver, ObservedWatch

from trail.entry import Entry
from trail.event import WatchdogEvent
from trail.node import Node
from trail.util import list_repr

if TYPE_CHECKING:
    from trail.trail import Trail

STOP: Final = object()


class Handler(FileSystemEventHandler, Node):
    _parent: Watchdog

    def on_any_event(self, event: FileSystemEvent) -> None:
        watchdog = self._parent
        # observer threads outlive the session that scheduled them
        loop = watchdog.loop
        queue = watchdog.queue
        if not loop or not queue or loop.is_closed():
            return
        record = asdict(event)
        record["src_path"] = fsdecode(event.src_path)
        record["dest_path"] = fsdecode(event.dest_path)
        event = WatchdogEvent(**record)
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(queue.put_nowait, event)


class Watchdog(Node):
    """
    Observes the directories backing the Trail's entries for filesystem events.

    >>> trail.watchdog
    Watchdog
        running: True
        watches: [
            '/tmp/tmpbzh09nb5/folder',
            '/tmp/tmpbzh09nb5',
            '/tmp/tmpbzh09nb5/folder/nested',
        ]
    """

    _parent: Trail
    debounce = 0.1
    # directories listed by __repr__ before the remainder is summarized
    repr_limit = 10

    def __repr__(self) -> str:
        # while dormant the membership is all that remains of the watches
        if self.watches is None:
            directories = list(self.dir2ids)
        else:
            directories = list(self.watches)
        lines = [
            type(self).__name__,
            f'    running: {self.running!r}',
        ]
        lines.extend(
            list_repr(
                'watches',
                (
                    str(directory)
                    for directory in directories[:self.repr_limit]
                ),
                len(directories),
            )
        )
        return '\n'.join(lines)

    @cached_property
    def handler(self) -> Handler:
        """Converts native filesystem events and schedules enqueueing on the asyncio loop."""
        return Handler(self)

    @cached_property
    def observer(self) -> BaseObserver | None:
        """Manages filesystem monitoring threads and dispatches events to the handler."""
        return None

    @cached_property
    def queue(self) -> asyncio.Queue[WatchdogEvent] | None:
        """Buffers events for the consumer; STOP ends consumption after pending events are applied."""
        return None

    @cached_property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """Runs the consumer and accepts queue callbacks submitted from observer threads."""
        return None

    @cached_property
    def watches(self) -> dict[Path, ObservedWatch] | None:
        """Maps directories to scheduled watch handles to prevent duplicates and allow unscheduling."""
        return None

    @cached_property
    def dir2ids(self) -> dict[Path, set[str]]:
        """
        Maps directories to the entry IDs requiring observation. Releasing the last ID removes
        the directory's watch. Outlives a session so the next start can recreate the watches.
        """
        return {}

    @cached_property
    def consumer(self) -> asyncio.Task[None] | None:
        """Background asyncio task that batches queued events and applies them to the Trail."""
        return None

    def watch(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        # while dormant, dir2ids alone records the directory and _start() schedules it
        if not self.ensure() or directory in self.watches:
            return
        self.watches[directory] = self.observer.schedule(
            self.handler,
            str(directory),
            recursive=False,
            event_filter=[
                FileCreatedEvent,
                FileModifiedEvent,
                FileDeletedEvent,
                FileMovedEvent,
                FileOpenedEvent,
                DirCreatedEvent,
                DirDeletedEvent,
                DirMovedEvent,
            ],
        )

    def release(
        self,
        directory: Path,
        identifier: str,
    ) -> None:
        ids = self.dir2ids.get(directory)
        if ids is None or identifier not in ids:
            return
        if len(ids) == 1:
            if self.watches is not None:
                watch = self.watches.get(directory)
                if watch:
                    with suppress(KeyError):
                        self.observer.unschedule(watch)
                    del self.watches[directory]
            del self.dir2ids[directory]
        else:
            ids.remove(identifier)

    def invalidate(self, directory: Path) -> None:
        if self.watches is None:
            return
        for path in tuple(self.watches):
            if path.is_relative_to(directory):
                with suppress(KeyError):
                    self.observer.unschedule(self.watches[path])
                del self.watches[path]

    @property
    def running(self) -> bool:
        """True while a consumer task is alive to drain the queue."""
        consumer = self.consumer
        return bool(consumer) and not consumer.done()

    def ensure(self) -> bool:
        """
        Start observing without a context manager, so a Trail kept alive in a notebook cell or a
        REPL begins tracking the moment an entry is added. Returns False when no asyncio loop is
        running, leaving the observer dormant until start() or context() is awaited.
        """
        if self.running:
            return True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._start_session()
        return True

    def _start_session(self) -> None:
        """Builds the active components a Watchdog session"""
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.observer = Observer()
        self.watches = {}
        try:
            # created before the watches so a reentrant watch() sees a running Watchdog
            self.consumer = asyncio.create_task(self.apply(), name="watchdog-consumer")
            for directory in self.dir2ids:
                self.watch(directory)
            self.observer.start()
        except BaseException:
            self.observer.stop()
            if self.observer.is_alive():
                self.observer.join(5)
            if self.consumer:
                self.consumer.cancel()
            self.clear()
            raise

    async def start(self) -> None:
        """Start the observer and drain the queue of events."""
        consumer = self.consumer
        if consumer:
            if not consumer.done():
                return
            # surface whatever killed the consumer, then rebuild the session
            await consumer
            self.clear()
        self._start_session()

    async def stop(self) -> None:
        """Stop the observer and drain the queue of events."""
        if not self.running:
            self.clear()
            return
        observer = self.observer
        observer.stop()
        if observer.is_alive():
            observer.join(5)
        if observer.is_alive():
            raise RuntimeError(f"Failed to stop observer {observer}")
        # Drain callbacks submitted by the observer before closing the queue.
        await asyncio.sleep(0)
        self.queue.put_nowait(STOP)
        try:
            await self.consumer
        finally:
            self.clear()

    @asynccontextmanager
    async def context(self) -> AsyncIterator[Self]:
        """Observe asynchronously, draining queued changes when the context exits.

        Optional: ensure() already starts the observer when entries are added under a running
        asyncio loop. Use this context when the queue must be drained deterministically at a
        known point rather than whenever the consumer happens to be scheduled.

        Stage changes after they appear in the log or after leaving this context.
        """
        await self.start()
        try:
            yield self
        finally:
            await self.stop()

    def clear(self) -> None:
        # native observer threads cannot be restarted, so the next start builds a new session;
        # dir2ids is untouched and carries the directory membership across
        self.consumer = None
        self.loop = None
        self.queue = None
        self.observer = None
        self.watches = None

    async def apply(self) -> None:
        """
        Iterate across the queue of events, applying them to the entries of the Trail so upcoming Events
        may find their corresponding Entry.
        """
        # bound to this session's queue so a clear() cannot strand the drain
        queue = self.queue
        while True:
            first = await queue.get()
            if first is STOP:
                return
            if self.debounce:
                await asyncio.sleep(self.debounce)

            stopped = False
            batch = [first]
            while True:
                try:
                    event = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if event is STOP:
                    stopped = True
                    break
                batch.append(event)

            trail = self._trail
            for event in batch:
                if event.apply(trail) is None:
                    continue
                # todo: event.apply should contain the logic for this, not watchdog.apply
                if event.is_directory and event.event_type in ("created", "moved"):
                    path = Path(event.dest_path or event.src_path)
                    if path.is_dir() and not trail._ignored(path):
                        try:
                            root = trail.dirs.get(path)
                            if root is None:
                                root = Entry.from_path(path, trail=trail)
                            resources = tuple(root.walk())
                        except (FileNotFoundError, NotADirectoryError):
                            continue
                        added = []
                        try:
                            for resource in resources:
                                if (
                                    resource._parent.id2entry.get(resource.id)
                                    is resource
                                ):
                                    continue
                                resource.add()
                                added.append(resource)
                        except Exception:
                            for resource in reversed(added):
                                resource.remove()
                            raise
                        for resource in added:
                            discovered = WatchdogEvent(
                                src_path=str(resource.path),
                                event_type="created",
                                is_directory=resource.path in trail.dirs,
                                is_synthetic=True,
                            )
                            discovered.apply(trail)
            if stopped:
                return
