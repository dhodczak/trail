from __future__ import annotations

import builtins
import re
import shlex
from collections import UserDict
from collections.abc import Iterable
from copy import copy
from datetime import UTC, datetime, time
from operator import ge, gt, le, lt
from pathlib import Path
from typing import Final, Literal

from trail.node import Node

# how a declared field is matched: an id by prefix, a path once resolved, text as written
type Kind = Literal['id', 'path', 'text']

# start:stop:step, each part optional, counted the way python counts
SLICE: Final = re.compile(r'(-?\d+)?:(-?\d+)?(?::(-?\d+)?)?')
# the field has to be an identifier, so an absolute path holding an operator still reads as a path
COMPARISON: Final = re.compile(
    r'(?P<field>[A-Za-z_]\w*)(?P<operator>==|!=|<=|>=|=|<|>)(?P<value>.*)',
    re.DOTALL,
)
EQUALITY: Final = ('=', '==')
ORDERINGS: Final = {
    '<': lt,
    '<=': le,
    '>': gt,
    '>=': ge,
}
TRUE: Final = frozenset(('true', 'yes', '1'))
FALSE: Final = frozenset(('false', 'no', '0'))


class Select[T: UserDict](Node):
    """
    Picks records out of the collection it hangs off and returns them as a copy of that
    collection: the same parent and cached state, but a data dict holding only what was picked.

        events.select('/path/to/file.csv')
        events.select('/path/to/file.csv "/path/to/other file.csv" -5:')
        events.select('id=c9f380c2 path=path/to/file.csv')
        events.select('st_size<1024', 'timestamp>=2026-09-22')

    The arguments are split the way a shell splits them, and the pieces apply as a chain, each
    selecting from what the one before it left. A run of alternatives, bare values or `=` on the
    one field, is the exception: all of them select from what preceded the run, and are pooled.

        events.select('a.csv b.csv -5:')      the last five records of either file
        events.select('-5: a.csv')            those of the last five records that are a.csv's
    """

    _parent: T

    def __init__(
            self,
            parent: T,
            fields: dict[str, Kind] | None = None,
    ) -> None:
        Node.__init__(self, parent)
        if fields is None:
            fields = {'id': 'id'}
        # the fields a bare value is tried against, in order; a field left out can still be
        # compared, and is matched by the type of what it holds
        self.fields = fields

    def __call__( self, item ) -> T:
        # the arguments read as one line, so a run of alternatives may span several of them
        return self.split(item)

    def split(self, item: str) -> T:
        """
        Splits the input string into individual items, handling quotes and spaces.
        It then calls each substring as a chain.
        """
        out = self.subset(self._parent.data)
        before = out
        run = None
        for token in shlex.split(item):
            field = self.alternative(token)
            pooled = (
                    field is not None
                    and field == run
            )
            if not pooled:
                before = out
            select = type(self)(before, self.fields)
            selected = select.dispatch(token)
            if pooled:
                selected = select.union(out, selected)
            out = selected
            run = field
        return out

    def slice(self, item: str) -> T:
        """Handles e.g. -5:, meaning the last 5 items"""
        keys = list(self._parent.data)
        cut = self.bounds(item)
        return self.subset(keys[cut])

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

    def lookup(self, item: str) -> T:
        """
        Handles when input is a path, id, etc. with no comparison. The declared fields are tried
        in order, and the first that any record holds the value in is the one matched on.
        """
        data = self._parent.data
        for field in self.fields:
            keys = [
                key
                for key, record in data.items()
                if self.equals(record, field, item)
            ]
            if keys:
                return self.subset(keys)
        return self.subset(())

    def dispatch(self, token: str) -> T:
        if SLICE.fullmatch(token):
            return self.slice(token)
        if COMPARISON.fullmatch(token):
            return self.comparison(token)
        return self.lookup(token)

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
        # a child built for the original, such as its jsonl or by_pos, holds the original as its
        # parent and would act on it in place of the copy; dropped, it is rebuilt on access
        for name, value in vars(collection).items():
            if (
                    isinstance(value, Node)
                    and vars(value).get('_parent') is collection
            ):
                del vars(out)[name]
        out.data = {
            key: collection.data[key]
            for key in keys
        }
        # the positions `by_pos` indexes by, which the shallow copy would otherwise share
        if 'ids' in vars(out):
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
        coerced = self.coerce(held, field, value)
        if self.fields.get(field) == 'id':
            # ids are only ever shown shortened, so a prefix is what there is to paste back
            return held.startswith(coerced)
        return held == coerced

    def held(
            self,
            record: object,
            field: str,
    ) -> object | None:
        out = getattr(record, field, None)
        # an empty string is how an event leaves a field unset, and its repr omits it likewise
        if (
                out is None
                or out == ''
        ):
            return None
        kind = self.fields.get(field)
        if kind == 'id':
            # a record held by reference, such as an event's entry, is named by its id
            return str(getattr(out, 'id', out))
        if kind == 'path':
            return Path(out)
        if kind == 'text':
            return str(out)
        return out

    def coerce(
            self,
            held: object,
            field: str,
            value: object,
    ) -> object:
        if not isinstance(value, str):
            return value
        if self.fields.get(field) == 'id':
            return value.removeprefix('#').lower()
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
    def alternative(token: str) -> str | None:
        # the run an alternative belongs to: '' for a bare value, the field for `=`; anything
        # else is None, and chains rather than pools
        if SLICE.fullmatch(token):
            return None
        match = COMPARISON.fullmatch(token)
        if match is None:
            return ''
        if match['operator'] in EQUALITY:
            return match['field']
        return None

    @staticmethod
    def parse(item: str) -> re.Match[str]:
        match = COMPARISON.fullmatch(item)
        if match is None:
            raise ValueError(f'not a comparison: {item!r}')
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
