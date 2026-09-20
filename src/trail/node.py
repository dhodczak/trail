from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trail.asset import Asset, Assets
    from trail.event import Event, Events
    from trail.trail import Trail
    from trail.watchdog import Handler, Watchdog


class Node:
    # todo: these should all be weakrefs

    def __init__(
        self,
        parent: Node | None = None,
    ) -> None:
        self._parent = parent

    @cached_property
    def _parent(self) -> Node:
        msg = f"Node {self} has no parent"
        raise AttributeError(msg)

    @cached_property
    def _event(self) -> Event:
        from trail.event import Event

        parent = self._parent
        if isinstance(parent, Event):
            return parent
        return parent._event

    @cached_property
    def _trail(self) -> Trail:
        from trail.trail import Trail

        parent = self._parent
        if isinstance(parent, Trail):
            return parent
        return parent._trail

    @cached_property
    def _assets(self) -> Assets:
        from trail.asset import Assets
        from trail.trail import Trail

        parent = self._parent
        if isinstance(parent, Trail):
            return parent.assets
        if isinstance(parent, Assets):
            return parent
        return parent._assets

    @cached_property
    def _asset(self) -> Asset:
        from trail.asset import Asset

        parent = self._parent
        if isinstance(parent, Asset):
            return parent
        return parent._asset

    @cached_property
    def _events(self) -> Events:
        from trail.event import Events
        from trail.trail import Trail

        parent = self._parent
        if isinstance(parent, Trail):
            return parent.events
        if isinstance(parent, Events):
            return parent
        return parent._events

    @cached_property
    def _watchdog(self) -> Watchdog:
        from trail.trail import Trail
        from trail.watchdog import Watchdog

        parent = self._parent
        if isinstance(parent, Trail):
            return parent.watchdog
        if isinstance(parent, Watchdog):
            return parent
        return parent._watchdog

    @cached_property
    def _handler(self) -> Handler:
        from trail.watchdog import Handler

        parent = self._parent
        if isinstance(parent, Handler):
            return parent
        return parent._handler

    @staticmethod
    def _setnested(
        obj: object,
        name: str,
        value: object,
    ):
        attrs = name.split(".")
        for attr in attrs[:-1]:
            obj = getattr(obj, attr)
        setattr(obj, attrs[-1], value)

    def __set_name__(self, owner: type, name: str) -> None:
        self.__name__ = name
