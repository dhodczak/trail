from __future__ import annotations

import json
from collections import UserDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from trail.node import Node
from trail.util import asset_repr, normalize_id

if TYPE_CHECKING:
    from trail.event import Event
    from trail.trail import Trail


@dataclass
class Checkpoint:
    id: str = field(default_factory=lambda: uuid4().hex, kw_only=True)
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(UTC),
        kw_only=True,
    )
    events: list[Event | str]
    message: str = ""
    author: str | None = None

    def __post_init__(self) -> None:
        self.id = normalize_id(self.id)

    @classmethod
    def from_record(cls, /, **record) -> Checkpoint:
        record["timestamp"] = datetime.fromisoformat(record["timestamp"])
        record['events'] = [
            normalize_id(identifier)
            for identifier in record['events']
        ]
        return cls(**record)

    def to_record(self) -> dict:
        event_ids = []
        for event in self.events:
            if isinstance(event, str):
                event_ids.append(event)
            else:
                event_ids.append(event.id)
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "events": event_ids,
            "message": self.message,
            "author": self.author,
        }


class JSONL(Node):
    _parent: Checkpoints

    def __repr__(self) -> str:
        return asset_repr(type(self).__name__, self.path)

    @property
    def path(self) -> Path | None:
        trail = self._trail
        if trail.dir:
            return trail.dir / "checkpoints.jsonl"
        return None

    def read(self) -> None:
        path = self.path
        if path is None or not path.exists():
            return
        loaded: dict[str, Checkpoint] = {}
        with path.open(encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                checkpoint = Checkpoint.from_record(**json.loads(line))
                if checkpoint.id in loaded:
                    raise ValueError(f"Duplicate commit ID in {path}: {checkpoint.id}")
                loaded[checkpoint.id] = checkpoint
        self._parent.data.clear()
        self._parent.data.update(loaded)

    def write(self) -> None:
        path = self.path
        if path is None:
            return
        text = "".join(
            json.dumps(checkpoint.to_record(), ensure_ascii=False) + "\n"
            for checkpoint in self._parent.values()
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def append(self, checkpoints: Iterable[Checkpoint]) -> None:
        path = self.path
        if path is None:
            return
        text = "".join(
            json.dumps(checkpoint.to_record(), ensure_ascii=False) + "\n"
            for checkpoint in checkpoints
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


class Checkpoints(UserDict[str, Checkpoint], Node):
    _parent: Trail

    @cached_property
    def jsonl(self) -> JSONL:
        return JSONL(self)

    def update(self, m, /) -> None:
        batch = dict(m)
        self.jsonl.append(batch.values())
        self.data.update(batch)

    def clear(self) -> None:
        super().clear()
        self.jsonl.write()

    def __setitem__(
        self,
        key: str,
        value: Checkpoint,
    ) -> None:
        if not isinstance(value, Checkpoint):
            raise TypeError(f"Expected Checkpoint, got {type(value).__name__}")
        self.jsonl.append([value])
        self.data[key] = value
