from __future__ import annotations

from typing import Final

from prompt_toolkit.styles import Style

PROMPT: Final = "> "
# the rule between two panes
VERTICAL: Final = "│"
# what a parameter line of an event is set in from the line naming the event
INDENT: Final = "    "

STYLE: Final = Style.from_dict(
    {
        "header": "bg:ansibrightblack ansiwhite",
        "header.name": "bold",
        "header.id": "ansibrightcyan",
        "header.mode": "ansiyellow",
        "header.counts": "ansiwhite",
        "separator": "ansibrightblack",
        "column": "bg:ansibrightblack ansiwhite",
        "column.index": "bg:ansiblue ansiwhite bold",
        "column.title": "bold",
        "column.id": "ansibrightcyan",
        "position": "ansibrightblack",
        "day": "ansibrightblack bold",
        "parameter": "ansibrightblack",
        "id": "ansibrightblack",
        "kind": "ansiblue",
        "missing": "ansired",
        "echo": "bold",
        "echo.prompt": "ansicyan bold",
        "info": "ansibrightblack",
        "error": "ansired bold",
        "event": "ansiblue",
        "event.registered": "ansigreen bold",
        "event.unregistered": "ansired bold",
        "event.created": "ansigreen",
        "event.discovered": "ansigreen",
        "event.modified": "ansiyellow",
        "event.deleted": "ansired",
        "event.moved": "ansicyan",
        "event.opened": "ansibrightblack",
        "text-area.prompt": "ansicyan bold",
    }
)

VERB_STYLES: Final[dict[str, str]] = {
    "registered": "class:event.registered",
    "unregistered": "class:event.unregistered",
    "created": "class:event.created",
    "discovered": "class:event.discovered",
    "modified": "class:event.modified",
    "deleted": "class:event.deleted",
    "moved": "class:event.moved",
    "opened": "class:event.opened",
}
