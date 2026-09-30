from __future__ import annotations

import shlex
from collections.abc import Callable, Iterable, Iterator, Sequence
from functools import cached_property
from glob import iglob
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

from prompt_toolkit import PromptSession, print_formatted_text, prompt
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    DynamicCompleter,
    PathCompleter,
    WordCompleter,
)
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    BufferControl,
    CompletionsMenu,
    ConditionalContainer,
    Dimension,
    Float,
    FloatContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.shortcuts import choice
from prompt_toolkit.validation import Validator

from trail.cli.command import MAGIC
from trail.cli.console import Console
from trail.cli.render import Renderer
from trail.cli.theme import PROMPT, STYLE
from trail.markers import PREMADE, Markers, premade
from trail.trail import Trail

WEBSITE: Final = "https://geojupyter.github.io/trail"
PREMADE_NAME: Final = "default"
# offered by the toggle console alongside whatever is already chosen
COMMON: Final[tuple[str, ...]] = (
    ".csv",
    ".tsv",
    ".txt",
    ".json",
    ".geojson",
    ".gpkg",
    ".shp",
    ".parquet",
    ".feather",
    ".tif",
    ".tiff",
    ".nc",
    ".h5",
    ".xlsx",
    ".pkl",
    ".npy",
    ".png",
    ".jpg",
)


def say(
        text: str,
        style: str = "",
) -> None:
    print_formatted_text(FormattedText([(style, text)]), style=STYLE)


def hint(*lines: str) -> None:
    for line in lines:
        say(f"  - {line}", "class:info")


def display(path: Path) -> str:
    text = Renderer.home(path)
    if path.is_dir():
        text += "/"
    return text


def bare(markers: Iterable[str]) -> str:
    """Markers the way the walkthrough spells them: sorted, spaced, and without their dots."""
    return " ".join(
        marker.removeprefix(".")
        for marker in sorted(markers)
    )


def marker(line: str) -> str | None:
    """The marker a line of a markers file spells; None for a comment, a blank or a bad line."""
    stripped = line.strip()
    if (
        not stripped
        or stripped.startswith("#")
    ):
        return None
    try:
        return Markers.normalize(stripped)
    except ValueError:
        return None


def parse(lines: Iterable[str]) -> set[str]:
    out = {
        marker(line)
        for line in lines
    }
    out.discard(None)
    return out


def compose(
        lines: Sequence[str],
        markers: set[str],
) -> str:
    """
    The text of a markers file holding exactly `markers`, built on `lines` so that their comments
    and ordering survive. A line that is not a valid extension is dropped along with the markers
    that were toggled off.
    """
    kept = [
        line
        for line in lines
        if (
            marker(line) in markers
            or not line.strip()
            or line.strip().startswith("#")
        )
    ]
    present = parse(kept)
    kept.extend(sorted(markers - present))
    text = "".join(
        f"{line}\n"
        for line in kept
    )
    return text


class Paths(PathCompleter):
    """
    PathCompleter, but a directory is completed through its slash, the way a shell completes
    one. PathCompleter leaves the slash off, so what is typed next runs on into the name.
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        for completion in super().get_completions(document, complete_event):
            if completion.display_text.endswith("/"):
                yield Completion(
                    f"{completion.text}/",
                    completion.start_position,
                    display=completion.display,
                )
            else:
                yield completion


def tabbing() -> KeyBindings:
    """
    Tab takes the completion under the cursor, or the first if none is, rather than moving on to
    the next; a directory taken then offers its own contents, the way a shell descends into one.
    """
    bindings = KeyBindings()

    @bindings.add("tab", filter=has_completions)
    def _take(event: KeyPressEvent) -> None:
        buffer = event.current_buffer
        state = buffer.complete_state
        completion = state.current_completion
        if completion is None:
            completion = state.completions[0]
        buffer.apply_completion(completion)
        if completion.text.endswith("/"):
            buffer.start_completion(select_first=False)

    return bindings


def expand(
        text: str,
        root: Path,
) -> Path:
    """The directory a typed path names, read from `root`; a `.trail` names its project."""
    path = (root / Path(text.strip()).expanduser()).resolve()
    if path.name == ".trail":
        path = path.parent
    return path


def respecify(
        suggested: Path,
        root: Path,
) -> Path:
    bindings = KeyBindings()

    # the arrow climbs to the parent, unless it is moving through completions
    @bindings.add("up", filter=~has_completions)
    def _up(event: KeyPressEvent) -> None:
        buffer = event.current_buffer
        try:
            parent = expand(buffer.text, root).parent
        except RuntimeError:
            return
        buffer.document = Document(str(parent))

    @bindings.add(Keys.BracketedPaste)
    def _paste(event: KeyPressEvent) -> None:
        event.current_buffer.insert_text(Console.pasted(event.data))

    def valid(text: str) -> bool:
        # '~nobody' raises rather than expanding, and a NUL raises on the stat
        try:
            return expand(text, root).is_dir()
        except (RuntimeError, ValueError):
            return False

    validator = Validator.from_callable(
        valid,
        error_message="not a directory",
        move_cursor_to_end=True,
    )
    completer = Paths(
        only_directories=True,
        expanduser=True,
        get_paths=lambda: [str(root)],
    )
    text = prompt(
        "  Trail directory: ",
        default=str(suggested),
        key_bindings=merge_key_bindings([bindings, tabbing()]),
        completer=completer,
        validator=validator,
        validate_while_typing=False,
    )
    return expand(text, root)


def directory(
        suggested: Path,
        root: Path,
) -> Path:
    say(f"Is {Renderer.home(suggested)} your intended Trail directory?", "bold")
    hint(
        "By default, Trail tracks any relevant assets in this directory or subdirectories.",
        "You can set up Trail to track external directories and assets later.",
        "Running Trail in this directory or subdirectories will restart the same Trail.",
    )
    options = [
        (True, "Yes!!!"),
        (False, "No, let me respecify:"),
    ]
    if choice("", options=options, default=True, style=STYLE):
        home = suggested
    else:
        home = respecify(suggested, root)
    enclosing = Trail.locate(home)
    if enclosing == home / ".trail":
        say(f"Re-initializing the Trail at {Renderer.home(home)}; its log is kept.", "class:info")
    elif enclosing is not None:
        outer = Renderer.home(enclosing.parent)
        say(f"This Trail will nest inside the one at {outer}.", "class:info")
    return home


class Picker:
    """
    The markers files a new `.markers` can start from, listed on the left beside a preview of
    the one under the cursor. Right takes the arrows into the preview, where a cursor of its
    own moves through the file's lines and the preview follows it; left gives them back to the
    listing. Typing a path offers a file of one's own instead, and the preview follows what is
    typed. Enter chooses whichever file the preview shows; escape turns them all down.
    """

    # terminal rows left to the question and its hints above the picker
    margin = 9

    def __init__(
        self,
        options: Sequence[tuple[str, Traversable]],
        root: Path,
    ) -> None:
        self.options = options
        self.root = root
        self.index = 0
        # the first row of the file the preview shows
        self.offset = 0
        # the row of the file the cursor is on while reading it
        self.line = 0
        # whether the arrows move through the file rather than the listing
        self.reading = False
        self.error = ""

    @cached_property
    def buffer(self) -> Buffer:
        completer = Paths(
            expanduser=True,
            get_paths=lambda: [str(self.root)],
        )
        return Buffer(
            completer=completer,
            multiline=False,
            on_text_changed=self._changed,
        )

    def _changed(self, buffer: Buffer) -> None:
        self.error = ""
        self.offset = 0
        self.line = 0

    @property
    def typed(self) -> str:
        return self.buffer.text.strip()

    @property
    def custom(self) -> Path | None:
        """The markers file the typed path names; a directory names the `.markers` inside it."""
        try:
            path = (self.root / Path(self.typed).expanduser()).resolve()
            if path.is_dir():
                path = path / ".markers"
            if path.is_file():
                return path
        except (RuntimeError, ValueError):
            pass
        return None

    @property
    def source(self) -> Traversable | None:
        if self.typed:
            return self.custom
        if not self.options:
            return None
        return self.options[self.index][1]

    def move(self, step: int) -> None:
        if self.reading:
            self.browse(step)
        else:
            self.scroll(step)

    def scroll(self, step: int) -> None:
        # the first press after typing gives the listing back rather than moving through it
        if self.buffer.text:
            self.buffer.text = ""
            return
        index = max(0, min(len(self.options) - 1, self.index + step))
        if index != self.index:
            self.index = index
            self.offset = 0
            self.line = 0

    def browse(self, step: int) -> None:
        rows = len(self._content())
        self.line = max(0, min(rows - 1, self.line + step))
        # the preview follows the cursor out of either end
        viewport = self.viewport
        if self.line < self.offset:
            self.offset = self.line
        elif self.line >= self.offset + viewport:
            self.offset = self.line - viewport + 1

    def accept(self) -> tuple[str, list[str]] | None:
        """The label and the lines of the file the preview shows; None, with a reason, if none."""
        source = self.source
        if source is None:
            if self.typed:
                self.error = f"no markers file at {self.typed}"
            else:
                self.error = "type a path, or press <ESCAPE> to go without"
            return None
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            self.error = str(error)
            return None
        if self.typed:
            label = Renderer.home(Path(str(source)))
        else:
            label = self.options[self.index][0]
        return label, lines

    @staticmethod
    def style(line: str) -> str:
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("#")
        ):
            return "class:info"
        if marker(line) is None:
            return "class:error"
        return "class:kind"

    def _listing(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        for index, (label, _) in enumerate(self.options):
            if index:
                out.append(("", "\n"))
            if (
                index == self.index
                and not self.typed
            ):
                # scrolls the window to the cursor once there are more files than rows
                out.append(("[SetCursorPosition]", ""))
                if self.reading:
                    out.append(("bold", f"  > {label}"))
                else:
                    out.append(("class:selected-option", f"  > {label}"))
            else:
                out.append(("", f"    {label}"))
        return out

    def _content(self) -> list[tuple[str, str]]:
        """The rows of the file the preview shows, as the style and the text of each."""
        source = self.source
        if source is None:
            return [("class:info", "no markers file at this path")]
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            return [("class:error", str(error))]
        if not lines:
            return [("class:info", "empty")]
        out = [
            (self.style(line), line)
            for line in lines
        ]
        return out

    def _preview(self) -> StyleAndTextTuples:
        rows = self._content()
        # the cursor is drawn as wide as the longest line, so that it shows on a blank one too
        width = max(
            len(text)
            for _, text in rows
        )
        viewport = self.viewport
        last = max(0, len(rows) - viewport)
        offset = min(self.offset, last)
        shown = rows[offset:offset + viewport]
        # padded to the viewport, so that the rule down the side runs the full height
        shown.extend(
            ("", "")
            for _ in range(viewport - len(shown))
        )
        out: StyleAndTextTuples = []
        for index, (style, text) in enumerate(shown):
            if index:
                out.append(("", "\n"))
            # the rule doubles as the scrollbar, pointing to where the file runs on
            if (
                index == 0
                and offset > 0
            ):
                out.append(("class:info", " ↑ "))
            elif (
                index == viewport - 1
                and offset < last
            ):
                out.append(("class:info", " ↓ "))
            else:
                out.append(("class:info", " │ "))
            if (
                self.reading
                and offset + index == self.line
            ):
                out.append((f"{style} reverse", text.ljust(max(width, 1))))
            else:
                out.append((style, text))
        return out

    @cached_property
    def depth(self) -> int:
        """The rows of the longest file offered, or of the listing if that is longer."""
        out = len(self.options)
        for _, source in self.options:
            try:
                count = len(source.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                continue
            out = max(out, count)
        return out

    @property
    def viewport(self) -> int:
        """
        The rows given to the listing and to the preview: enough for the longest file offered, so
        that scrolling through the listing does not resize the picker, or for a longer one typed,
        but never more than the terminal holds below the question.
        """
        longest = max(self.depth, len(self._content()))
        rows = get_app().output.get_size().rows
        return max(1, min(longest, rows - self.margin))

    @cached_property
    def layout(self) -> Layout:
        width = max(
            (
                len(label)
                for label, _ in self.options
            ),
            default=0,
        )
        # an inline application is handed every row below the cursor; neither of these may
        # take more than its content, or the picker fills the terminal
        body = VSplit(
            [
                Window(
                    FormattedTextControl(self._listing),
                    width=width + 4,
                    height=lambda: Dimension(max=self.viewport),
                    dont_extend_height=True,
                ),
                Window(
                    FormattedTextControl(self._preview),
                    height=lambda: Dimension(max=self.viewport),
                    wrap_lines=False,
                    dont_extend_height=True,
                ),
            ]
        )
        field = Window(
            BufferControl(
                self.buffer,
                input_processors=[BeforeInput("  path: ", style="class:info")],
            ),
            height=1,
        )
        error = ConditionalContainer(
            Window(
                FormattedTextControl(lambda: [("class:error", f"  {self.error}")]),
                height=1,
            ),
            filter=Condition(lambda: bool(self.error)),
        )
        menu = Float(
            xcursor=True,
            ycursor=True,
            content=CompletionsMenu(max_height=8, scroll_offset=1),
        )
        container = FloatContainer(
            HSplit([body, field, error]),
            floats=[menu],
        )
        return Layout(container, focused_element=field)

    @cached_property
    def bindings(self) -> KeyBindings:
        bindings = KeyBindings()
        idle = ~has_completions
        empty = Condition(lambda: not self.buffer.text)

        @bindings.add("up", filter=idle)
        def _up(event: KeyPressEvent) -> None:
            self.move(-1)

        @bindings.add("down", filter=idle)
        def _down(event: KeyPressEvent) -> None:
            self.move(1)

        # once a path is typed, left and right move through it instead
        @bindings.add("left", filter=idle & empty)
        def _left(event: KeyPressEvent) -> None:
            self.reading = False

        @bindings.add("right", filter=idle & empty)
        def _right(event: KeyPressEvent) -> None:
            self.reading = True

        # a completion under the cursor already stands in the buffer; enter keeps it
        @bindings.add("enter", filter=has_completions)
        def _keep(event: KeyPressEvent) -> None:
            event.current_buffer.complete_state = None

        # and escape takes it back, rather than turning every markers file down
        @bindings.add("escape", filter=has_completions, eager=True)
        def _revert(event: KeyPressEvent) -> None:
            event.current_buffer.cancel_completion()

        @bindings.add("enter", filter=idle)
        def _accept(event: KeyPressEvent) -> None:
            chosen = self.accept()
            if chosen is not None:
                event.app.exit(result=chosen)

        @bindings.add("escape", filter=idle, eager=True)
        def _reject(event: KeyPressEvent) -> None:
            event.app.exit(result=None)

        @bindings.add("c-c")
        def _interrupt(event: KeyPressEvent) -> None:
            event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

        @bindings.add("c-d", filter=empty)
        def _end(event: KeyPressEvent) -> None:
            event.app.exit(exception=EOFError, style="class:aborting")

        @bindings.add(Keys.BracketedPaste)
        def _paste(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text(Console.pasted(event.data))

        return bindings

    @cached_property
    def application(self) -> Application[tuple[str, list[str]] | None]:
        return Application(
            layout=self.layout,
            key_bindings=merge_key_bindings([self.bindings, tabbing()]),
            style=STYLE,
            erase_when_done=True,
        )

    def run(self) -> tuple[str, list[str]] | None:
        return self.application.run()


def offer() -> list[tuple[str, Traversable]]:
    """What the picker lists: the premade files, the default first."""
    names = sorted(
        premade(),
        key=lambda name: (name != PREMADE_NAME, name),
    )
    out: list[tuple[str, Traversable]] = [
        (name, PREMADE / f"{name}.markers")
        for name in names
    ]
    return out


class Asked(Completer):
    """
    Another completer, held back from an empty word unless tab asks for it. A prompt that stays
    open is emptied by every submission, which would otherwise open the whole list each time.
    """

    def __init__(self, completer: Completer) -> None:
        self.completer = completer

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        word = document.get_word_before_cursor(WORD=True)
        if (
            not word
            and not complete_event.completion_requested
        ):
            return
        yield from self.completer.get_completions(document, complete_event)


def ask(
        message: Callable[[], StyleAndTextTuples],
        request: Callable[[], str],
        submit: Callable[[str], None],
        completer: Completer,
        multiline: bool = False,
        extra: KeyBindings | None = None,
) -> None:
    """
    A prompt that stays open across submissions. Each one is handed to `submit` and the prompt
    emptied, so that `message`, which shows what the submissions have made, is redrawn in place
    rather than printed again. `request` asks for the next one, with the cursor on the line
    below it. Only an empty submission closes the prompt, and it is given to `submit` as well, so
    that notes left by the last one are cleared before `message` is printed for good. `extra`
    is consulted before the submission on enter, so that its own bindings may claim the key.
    """
    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event: KeyPressEvent) -> None:
        buffer = event.current_buffer
        submit(buffer.text)
        if buffer.text.strip():
            buffer.reset()
        else:
            buffer.validate_and_handle()

    # the last binding matching a key is the one that handles it
    merged = [bindings, tabbing()]
    if extra is not None:
        merged.append(extra)
    bindings = merge_key_bindings(merged)

    def framed() -> StyleAndTextTuples:
        out = message()
        out.append(("", f"  {request()}\n"))
        out.append(("class:echo.prompt", f"  {PROMPT}"))
        return out

    # the request and the cursor are spent once the prompt closes, so the prompt is erased and
    # only what the submissions made is left on screen
    session = PromptSession(erase_when_done=True, style=STYLE)
    # multiline only so that a paste keeps its lines; typed, enter submits
    session.prompt(
        framed,
        key_bindings=bindings,
        completer=Asked(completer),
        multiline=multiline,
        prompt_continuation=" " * (2 + len(PROMPT)),
    )
    print_formatted_text(FormattedText(message()), style=STYLE, end="")


def toggle(markers: set[str]) -> set[str]:
    """
    The markers once the user is done toggling them. Each submission toggles what it names and
    redraws the line of markers in place; only an empty submission moves on.
    """
    out = set(markers)
    errors: list[str] = []

    def message() -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        if out:
            fragments.append(("class:kind", f"  {bare(out)}\n"))
        else:
            fragments.append(("class:info", "  none\n"))
        fragments.extend(
            ("class:error", f"  {error}\n")
            for error in errors
        )
        return fragments

    def submit(text: str) -> None:
        errors.clear()
        for token in text.split():
            try:
                extension = Markers.normalize(token)
            except ValueError as error:
                errors.append(str(error))
                continue
            if extension in out:
                out.remove(extension)
            else:
                out.add(extension)

    # the common extensions and whichever are marked, read afresh as the marks change
    def offered() -> Completer:
        words = bare(set(COMMON) | out).split()
        return WordCompleter(words, WORD=True)

    def request() -> str:
        return "Enter an extension (or <ENTER> to continue):"

    ask(message, request, submit, DynamicCompleter(offered))
    return out


def resolve(
        line: str,
        root: Path,
        errors: list[str],
) -> list[Path]:
    """
    The paths one pasted line names, with whatever it names that does not exist added to
    `errors`. A line naming an existing path is that path, spaces and all; anything else is
    split the way a shell would, so `ls` output pastes as well as a list.
    """
    whole = root / Path(line).expanduser()
    if whole.exists():
        return [whole.resolve()]
    out: list[Path] = []
    for token in shlex.split(line):
        expanded = Path(token).expanduser()
        if any(
            char in token
            for char in MAGIC
        ):
            matches = sorted(
                (root / match).resolve()
                for match in iglob(str(expanded), root_dir=root, recursive=True)
            )
            if not matches:
                errors.append(f"no matches: {token}")
            out.extend(matches)
            continue
        path = (root / expanded).resolve()
        if path.exists():
            out.append(path)
        else:
            errors.append(f"no such path: {token}")
    return out


def gather(
        home: Path,
        root: Path,
) -> list[Path]:
    """
    The paths to track, starting from the Trail directory. Each submission adds what it names
    and redraws the listing in place; enter submits the lines of a paste together. The arrows
    take a cursor into the listing, where enter removes the path under it, the Trail directory
    included; typing, escape, or moving down past the last path gives the cursor back to the
    prompt. Only an empty submission from the prompt moves on.
    """
    metadata = home / ".trail"
    selected: dict[Path, None] = {home: None}
    notes: list[tuple[str, str]] = []
    # the index into `selected` of the path under the cursor; None while at the prompt
    cursor: int | None = None
    browsing = Condition(lambda: cursor is not None)
    idle = ~has_completions & Condition(lambda: not get_app().current_buffer.text)

    def message() -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        if not selected:
            fragments.append(("class:info", "  none\n"))
        for index, path in enumerate(selected):
            if index == cursor:
                fragments.append(("class:selected-option", f"> {display(path)}\n"))
            else:
                fragments.append(("class:kind", f"  {display(path)}\n"))
        fragments.extend(
            (style, f"  {note}\n")
            for style, note in notes
        )
        return fragments

    def request() -> str:
        if cursor is not None:
            return "Press <ENTER> to remove this path, or <ESCAPE> to return:"
        if selected:
            return "Enter a path, <UP> to remove one, or <ENTER> to continue:"
        return "Enter a path (or <ENTER> to continue):"

    bindings = KeyBindings()

    @bindings.add("up", filter=idle)
    def _up(event: KeyPressEvent) -> None:
        nonlocal cursor
        if not selected:
            return
        if cursor is None:
            cursor = len(selected) - 1
        else:
            cursor = max(0, cursor - 1)
        notes.clear()

    @bindings.add("down", filter=idle & browsing)
    def _down(event: KeyPressEvent) -> None:
        nonlocal cursor
        if cursor == len(selected) - 1:
            cursor = None
        else:
            cursor += 1

    @bindings.add("enter", filter=browsing)
    def _remove(event: KeyPressEvent) -> None:
        nonlocal cursor
        path = list(selected)[cursor]
        del selected[path]
        if not selected:
            cursor = None
        else:
            cursor = min(cursor, len(selected) - 1)

    @bindings.add("escape", filter=browsing, eager=True)
    def _return(event: KeyPressEvent) -> None:
        nonlocal cursor
        cursor = None

    # typing hands the keys back to the prompt, starting with the one typed
    @bindings.add(Keys.Any, filter=browsing)
    def _type(event: KeyPressEvent) -> None:
        nonlocal cursor
        cursor = None
        event.current_buffer.insert_text(event.data)

    @bindings.add(Keys.BracketedPaste, filter=browsing)
    def _paste(event: KeyPressEvent) -> None:
        nonlocal cursor
        cursor = None
        event.current_buffer.insert_text(Console.pasted(event.data))

    def submit(text: str) -> None:
        notes.clear()
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            errors: list[str] = []
            try:
                paths = resolve(line, root, errors)
            except ValueError as error:
                notes.append(("class:error", f"{line}: {error}"))
                continue
            notes.extend(
                ("class:error", error)
                for error in errors
            )
            for path in paths:
                if path.is_relative_to(metadata):
                    notes.append(("class:error", f"{display(path)} is Trail metadata"))
                elif path in selected:
                    notes.append(("class:info", f"{display(path)} is already listed"))
                else:
                    selected[path] = None

    completer = Paths(
        expanduser=True,
        get_paths=lambda: [str(root)],
    )
    ask(message, request, submit, completer, multiline=True, extra=bindings)
    return list(selected)


def startup(trail: Trail) -> None:
    """Placeholder: run `trail` in the background on login. Not yet asked or implemented."""


def init(root: Path) -> Trail:
    """
    Walk through starting a Trail, or re-initializing one, with relative paths read from `root`.
    Every question is asked before anything is written, so an interrupt leaves the project
    untouched. Re-initializing rewrites the markers and adds to what is tracked, offtrailing
    only the Trail directory if it was removed from the listing; the log is kept.
    """
    existing = Trail.locate(root)
    if existing is None:
        suggested = root
    else:
        suggested = existing.parent

    say("")
    say("🌲 Welcome to Trail! 🌳", "bold")
    say("")
    say("Trail leaves breadcrumbs along your data journey: the interactions git never sees,")
    say("such as opening a dataset, viewing it on a map, or carrying it between tools, so you")
    say("can retrace your project's steps.")
    say(f"Read more at {WEBSITE}", "class:info")
    say("")
    say("Let's start a Trail! 🥾", "bold")
    say("")

    home = directory(suggested, root)
    say("")

    path = home / ".markers"
    say("Markers allow for automatic tracking of assets such as `.csv` or `.txt`. 🪧", "bold")
    hint(
        "Scroll and press <ENTER> to select a premade marker set. 🪨",
        "Press <RIGHT> to scroll through a set's markers, and <LEFT> to return to the sets.",
        "Enter a specific path to select your own custom markers.",
        "Press <ESCAPE> to reject premade markers.",
        "Next, you can specify markers for specific extensions.",
    )
    say("")
    chosen = Picker(offer(), root).run()
    if chosen is None:
        lines = []
        say("  no premade markers", "class:kind")
    else:
        lines = chosen[1]
        say(f"  markers from {chosen[0]}", "class:kind")
    say("")

    say("Here's the extensions we've marked for following 🏷️:", "bold")
    hint(
        "Type an extension name and press <ENTER> to toggle it.",
        "You can always edit the `.markers` text file afterward.",
    )
    say("")
    markers = toggle(parse(lines))
    say("")

    say("Here's the directories and assets we're ready to track.", "bold")
    hint(
        "Enter a specific path to add it; several can be entered or pasted at once.",
        "Scroll up through the list and press <ENTER> to remove the path under the cursor.",
        "Press <ENTER> on an empty prompt to continue.",
        "You can always use `trail track` and `trail offtrail` to add or remove paths later.",
    )
    say("")
    paths = gather(home, root)
    say("")

    # `trail` on startup is asked here once `startup` is implemented

    path.write_text(compose(lines, markers), encoding="utf-8")
    # the markers are on file before the Trail opens, so the files they mark are tracked with
    # the project directory rather than after it; naming .trail itself opens the directory
    # chosen, even nested in another Trail
    trail = Trail(home / ".trail", markers=None, tracked=home in paths)
    # a Trail being re-initialized already tracks its directory, which may have been removed
    if (
        home not in paths
        and home in trail.entries
    ):
        trail.offtrail(home)
    for tracked in paths:
        if tracked in trail.entries:
            continue
        try:
            trail.track(tracked)
        except (OSError, ValueError) as error:
            say(f"{tracked}: {error}", "class:error")

    say(f"Trail started in {Renderer.home(home)} 🌲", "bold")
    say(f"  markers: {bare(trail.markers) or 'none'}", "class:info")
    say(f"  tracked: {len(trail.assets)} assets, {len(trail.dirs)} dirs", "class:info")
    say("")
    return trail
