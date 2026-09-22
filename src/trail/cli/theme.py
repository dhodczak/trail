from __future__ import annotations

from typing import Final

from prompt_toolkit.styles import Style

PROMPT: Final = "> "
# how far a parameter line is indented under the line naming its event
INDENT: Final = "    "

STYLE: Final = Style.from_dict(
    {
        "header": "bg:ansibrightblack ansiwhite",
        "header.name": "bold",
        "header.id": "ansibrightcyan",
        "header.mode": "ansiyellow",
        "header.counts": "ansiwhite",
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
        "event.tracked": "ansigreen bold",
        "event.offtrailed": "ansired bold",
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
    "tracked": "class:event.tracked",
    "offtrailed": "class:event.offtrailed",
    "created": "class:event.created",
    "discovered": "class:event.discovered",
    "modified": "class:event.modified",
    "deleted": "class:event.deleted",
    "moved": "class:event.moved",
    "opened": "class:event.opened",
}
