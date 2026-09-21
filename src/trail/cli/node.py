from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from trail.node import Node as Base

if TYPE_CHECKING:
    from trail.cli.column import Columns
    from trail.cli.console import Console
    from trail.cli.feed import Feed
    from trail.cli.render import Renderer


class Node(Base):
    """
    A Node that also walks to what the console is made of, the way the base class walks to the
    Trail. Holding a parent is only worth anything if reaching past it is the collection's
    business rather than each member's: a pane, a command or a collection asks for the renderer
    and gets it, whichever of them happens to be sitting in between.
    """

    @cached_property
    def _console(self) -> Console:
        from trail.cli.console import Console

        parent = self._parent
        if isinstance(parent, Console):
            return parent
        return parent._console

    @cached_property
    def _feed(self) -> Feed:
        """The console's own feed, where a command's output goes, never a pane's."""
        from trail.cli.console import Console

        parent = self._parent
        if isinstance(parent, Console):
            return parent.feed
        return parent._feed

    @cached_property
    def _columns(self) -> Columns:
        from trail.cli.column import Columns
        from trail.cli.console import Console

        parent = self._parent
        if isinstance(parent, Columns):
            return parent
        if isinstance(parent, Console):
            return parent.columns
        return parent._columns

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
