from __future__ import annotations

import builtins
import re
import shlex
from collections import deque
from collections.abc import Callable, Iterable
from copy import copy
from datetime import UTC, datetime, time
from functools import cached_property
from operator import ge, gt, le, lt
from pathlib import Path
from typing import Any, Final, Protocol, Self, overload

from trail.node import Node

# start:stop:step, each part optional, counted the way python counts
SLICE: Final = re.compile(r'(-?\d+)?:(-?\d+)?(?::(-?\d+)?)?')
COMPARISON: Final = re.compile(
    r'(?P<field>[A-Za-z_]\w*)(?P<operator>==|!=|<=|>=|=|<|>)(?P<value>.*)',
    re.DOTALL,
)
EQUALITY: Final = ('=', '==')
ORDERINGS: Final[dict[str, Callable[[Any, Any], bool]]] = {
    '<': lt,
    '<=': le,
    '>': gt,
    '>=': ge,
}
TRUE: Final = frozenset(('true', 'yes', '1'))
FALSE: Final = frozenset(('false', 'no', '0'))
PARENTHESES: Final = frozenset('()')


class Collection[V](Protocol):
    # what Select needs of the collection it hangs off: the records by key, and the keys in the
    # order a position counts them
    data: dict[str, V]
    ids: list[str]


class Select[T: Collection[Any], V](Node):
    """
    Picks records out of the collection it hangs off and returns them as a copy of that
    collection: the same parent, but a data dict holding only what was picked.

        events.select('src_path=/path/to/file.csv')
        events.select('id=8a43da699bf242e7976e7bb74df81d0f or id=c2152a8b8f31416abbfe7106fce8cb6c')
        events.select('st_size<1024 and not event_type=opened')
        events.select('(src_path=/path/to/a.csv or src_path=/path/to/b.csv) -5:')
        events.select('cls=WatchdogEvent event_type=opened')

    Every term names its field, and the value is read as whatever type the record holds there;
    a record held by reference, such as an event's entry, compares by its id. `cls` is the
    record's class, and `cls=Event` holds for its subclasses too, as isinstance would. `not`
    binds tightest, then `and`, then `or`, and terms written side by side are joined by `and`.

    A slice is a term like any other. Under `and`, each term selects from what the one before it
    left, so the order they are written in matters:

        events.select('src_path=/path/to/a.csv -5:')    the last five of that file's records
        events.select('-5: src_path=/path/to/a.csv')    those of the last five that are that file's

    Indexed by position, it gives the record there, or a copy holding a slice of them.

        events.select[0]                                the first record
        events.select[-5:]                              a copy holding the last five
    """

    _parent: T

    def __init__(self, parent: T) -> None:
        Node.__init__(self)
        # assigned rather than passed, since Node.__init__ takes a Node and T is typed only by
        # what Select uses of it
        self._parent = parent

    def __call__(self, item: str) -> T:
        return self.split(item)

    @overload
    def __getitem__(self, item: int) -> V: ...

    @overload
    def __getitem__(self, item: builtins.slice) -> T: ...

    def __getitem__(self, item: int | builtins.slice) -> V | T:
        collection = self._parent
        if isinstance(item, builtins.slice):
            keys = collection.ids[item]
            return self.subset(keys)
        identifier = collection.ids[item]
        return collection.data[identifier]

    def split(self, item: str) -> T:
        """Splits the input string into its terms, then calls them as a chain."""
        pending = self.lex(item)
        if not pending:
            return self[:]
        out = self.disjunction(pending)
        if pending:
            raise ValueError(f'unmatched {pending[0]!r} in {item!r}')
        return out

    def disjunction(self, pending: deque[str]) -> T:
        # every alternative selects from the same records, and what they select is pooled
        out = self.conjunction(pending)
        while self.peek(pending) == 'or':
            pending.popleft()
            alternative = self.conjunction(pending)
            out = self.union(out, alternative)
        return out

    def conjunction(self, pending: deque[str]) -> T:
        # each term selects from what the one before it left, which is how a slice cuts only
        # what the terms before it kept
        out = self.term(pending)
        while self.peek(pending) not in (None, 'or', ')'):
            if self.peek(pending) == 'and':
                pending.popleft()
            select = self.within(out)
            out = select.term(pending)
        return out

    def within(self, collection: T) -> Self:
        # the select a later term reads with; a subclass holding state of its own passes it on
        return type(self)(collection)

    def term(self, pending: deque[str]) -> T:
        if not pending:
            raise ValueError('the expression ends where a term should be')
        token = pending.popleft()
        if token == 'not':
            excluded = self.term(pending)
            keys = [
                key
                for key in self._parent.data
                if key not in excluded.data
            ]
            return self.subset(keys)
        if token == '(':
            out = self.disjunction(pending)
            if self.peek(pending) != ')':
                raise ValueError("unclosed '('")
            pending.popleft()
            return out
        if token in ('and', 'or', ')'):
            raise ValueError(f'expected a term, got {token!r}')
        if SLICE.fullmatch(token):
            return self.slice(token)
        return self.comparison(token)

    def slice(self, item: str) -> T:
        """Handles e.g. -5:, meaning the last 5 items"""
        cut = self.bounds(item)
        return self[cut]

    def comparison(self, item: str) -> T:
        """Handles comparison operators like id=... or timestamp>=... or st_size<... or path!="""
        match = self.parse(item)
        if match['field'] == 'timestamp':
            return self.timestamp_comparison(item)
        return self.where(match['field'], match['operator'], match['value'])

    def timestamp_comparison(self, item: str) -> T:
        """Handles timestamp comparisons like timestamp>=... or timestamp<..."""
        match = self.parse(item)
        # parsed once here rather than once for every record it is compared against
        moment = self.moment(match['value'])
        return self.where(match['field'], match['operator'], moment)

    def union(self, *selections: T) -> T:
        keys = [
            key
            for key in self._parent.data
            if any(
                key in selection.data
                for selection in selections
            )
        ]
        return self.subset(keys)

    def where(
            self,
            field: str,
            operator: str,
            value: object,
    ) -> T:
        keys = [
            key
            for key, record in self._parent.data.items()
            if self.compare(record, field, operator, value)
        ]
        return self.subset(keys)

    def subset(self, keys: Iterable[str]) -> T:
        collection = self._parent
        out = copy(collection)
        # whatever the original cached, such as its jsonl or path2entry, was built from it and
        # is shared by the shallow copy; dropped, it is rebuilt from the copy on access
        cached = [
            name
            for name in vars(out)
            if (
                    name != '_parent'
                    and isinstance(getattr(type(out), name, None), cached_property)
            )
        ]
        for name in cached:
            del vars(out)[name]
        out.data = {
            key: collection.data[key]
            for key in keys
        }
        out.ids = list(out.data)
        return out

    def compare(
            self,
            record: object,
            field: str,
            operator: str,
            value: object,
    ) -> bool:
        if operator in EQUALITY:
            return self.equals(record, field, value)
        if operator == '!=':
            # the negation of `=`, so a record without the field holds it, and
            # `event_type!=opened` keeps the events that have no event_type at all
            return not self.equals(record, field, value)
        held = self.held(record, field)
        if held is None:
            return False
        if isinstance(held, type):
            raise TypeError(f'{field} is compared only by = and !=')
        coerced = self.coerce(held, field, value)
        return ORDERINGS[operator](held, coerced)

    def equals(
            self,
            record: object,
            field: str,
            value: object,
    ) -> bool:
        held = self.held(record, field)
        if (
                held is None
                or value == ''
        ):
            return False
        if isinstance(held, type):
            # a class answers to its own name and to those of the classes it derives from, as
            # isinstance would
            return any(
                base.__name__ == value
                for base in held.__mro__
            )
        coerced = self.coerce(held, field, value)
        return held == coerced

    @staticmethod
    def held(
            record: object,
            field: str,
    ) -> object | None:
        # `cls` is the record's class, the name its stored record gives it
        if field == 'cls':
            return type(record)
        out = getattr(record, field, None)
        # an empty string is how an event leaves a field unset, and its repr omits it likewise
        if (
                out is None
                or out == ''
        ):
            return None
        # a record held by reference, such as an event's entry, is compared by its id
        return getattr(out, 'id', out)

    def coerce(
            self,
            held: object,
            field: str,
            value: object,
    ) -> object:
        if not isinstance(value, str):
            return value
        if isinstance(held, Path):
            return Path(value).expanduser().resolve()
        if isinstance(held, datetime):
            return self.moment(value)
        if isinstance(held, bool):
            lowered = value.lower()
            if lowered in TRUE:
                return True
            if lowered in FALSE:
                return False
            raise ValueError(f'{field}: not a boolean: {value!r}')
        if isinstance(held, int | float):
            return float(value)
        return value

    @staticmethod
    def lex(item: str) -> deque[str]:
        """Splits an expression the way a shell splits a line, every parenthesis a term alone."""
        lexer = shlex.shlex(item, posix=True, punctuation_chars='()')
        lexer.whitespace_split = True
        lexer.commenters = ''
        out: deque[str] = deque()
        for token in lexer:
            # a run of parentheses is lexed as one token
            if set(token) <= PARENTHESES:
                out.extend(token)
            else:
                out.append(token)
        return out

    @staticmethod
    def peek(pending: deque[str]) -> str | None:
        if pending:
            return pending[0]
        return None

    @staticmethod
    def parse(item: str) -> re.Match[str]:
        match = COMPARISON.fullmatch(item)
        if match is None:
            raise ValueError(f'{item!r} is not a comparison; name its field, as in id={item}')
        return match

    @staticmethod
    def moment(value: str) -> datetime:
        # a time alone is today's, since today is when a timestamp's repr leaves the date off; a
        # value without a zone is read as UTC, which records are stamped in and their reprs print
        try:
            out = datetime.fromisoformat(value)
        except ValueError:
            today = datetime.now(UTC).date()
            clock = time.fromisoformat(value)
            out = datetime.combine(today, clock)
        if out.tzinfo is None:
            out = out.replace(tzinfo=UTC)
        return out

    @staticmethod
    def bounds(item: str) -> builtins.slice:
        match = SLICE.fullmatch(item)
        if match is None:
            raise ValueError(f'not a slice: {item!r}')
        parts = []
        for part in match.groups():
            if part is None:
                parts.append(None)
            else:
                parts.append(int(part))
        return builtins.slice(*parts)
