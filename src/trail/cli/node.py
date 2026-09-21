from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from trail.node import Node as Base

if TYPE_CHECKING:
    from trail.cli.console import Console
    from trail.cli.feed import Feed
    from trail.cli.render import Renderer


class Node(Base):
    """ Walks up the parent chain to the console's parts, the way the base Node walks up to the Trail. """

    @cached_property
    def _console(self) -> Console:
        from trail.cli.console import Console

        parent = self._parent
        if isinstance(parent, Console):
            return parent
        return parent._console

    @cached_property
    def _feed(self) -> Feed:
        """The console's feed, where a command's output goes."""
        from trail.cli.console import Console

        parent = self._parent
        if isinstance(parent, Console):
            return parent.feed
        return parent._feed

    @cached_property
    def _renderer(self) -> Renderer:
        from trail.cli.console import Console
        from trail.cli.render import Renderer

        parent = self._parent
        if isinstance(parent, Renderer):
            return parent
        if isinstance(parent, Console):
            return parent.renderer
        return parent._renderer
