from __future__ import annotations

from collections.abc import Iterator
from typing import Any, TYPE_CHECKING, Protocol, Self, overload

from trail.node import Node
from trail.select import Select
from trail.util import items_repr

if TYPE_CHECKING:
    from trail.trail import Trail


class Record(Protocol):
    # what a collection needs of what it holds: the id it is kept under
    id: str


class Collection[K, V: Record](Node):
    """
    What a Trail keeps, held by the id of each record and iterated as the records themselves.

    `data` maps every id to its record and `ids` holds the order they were recorded in. The
    mapping is how a collection is stored rather than what it is, so iteration, membership and
    length speak of records rather than of keys, and `K` is whatever a record may be addressed
    by: its id alone for the log, a path as well for the entries.
    """

    _parent: Trail

    def __init__(self, parent: Trail | None = None) -> None:
        Node.__init__(self, parent)
        self.data: dict[str, V] = {}
        self.ids: list[str] = []

    @property
    def select(self) -> Select[Collection[K, V], Any]:
        # a property, since a cached_property leaves Self unbound and `assets.select(...)` would
        # type as Any rather than Assets
        return Select(self)

    def __iter__(self) -> Iterator[V]:
        # the records are taken up front, so that removing one while iterating over them, as
        # offtrailing does, does not take the records after it along with it
        records = [
            self.data[identifier]
            for identifier in self.ids
        ]
        return iter(records)

    def __len__(self) -> int:
        return len(self.data)

    def __contains__(self, key: K | V) -> bool:
        # a record answers for the id it is held under, so `event in events` holds wherever
        # `event.id in events` does
        return getattr(key, "id", key) in self.data

    def __getitem__(self, key: K) -> V:
        return self.data[key]

    @overload
    def get(self, key: K) -> V | None: ...

    @overload
    def get[D](
        self,
        key: K,
        default: D,
    ) -> V | D: ...

    def get[D](
        self,
        key: K,
        default: D | None = None,
    ) -> V | D | None:
        try:
            return self[key]
        except KeyError:
            return default

    def append(self, record: V) -> None:
        """Hold `record` under the id it carries."""
        self[record.id] = record

    def __setitem__(
        self,
        key: str,
        value: V,
    ) -> None:
        raise NotImplementedError

    def __delitem__(self, key: K) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        self.data.clear()
        self.ids.clear()

    def __repr__(self) -> str:
        return items_repr(type(self).__name__, self)
