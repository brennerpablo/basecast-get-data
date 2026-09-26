"""Grid filters as query params: `filter=<column>:<op>:<value>`, repeatable, AND-combined.

Lists (`in`, `between`) join their values with U+001F, the separator the app's grid-params uses, since
commas and colons show up in real values.
"""

from __future__ import annotations

from dataclasses import dataclass

SEP = "\x1f"
OPS = {"eq", "ne", "contains", "starts", "in", "gte", "lte", "gt", "lt", "between", "null", "notnull"}
NO_VALUE = {"null", "notnull"}


class FilterError(ValueError):
    pass


@dataclass(frozen=True)
class Filter:
    column: str
    op: str
    values: tuple[str, ...]

    @property
    def value(self) -> str:
        return self.values[0]


def parse_filters(raw: list[str], columns: set[str]) -> list[Filter]:
    out = []
    for item in raw:
        column, sep, rest = item.partition(":")
        op, _, value = rest.partition(":")
        if not sep or not column or not op:
            raise FilterError(f"filter must look like column:op:value, got {item!r}")
        if column not in columns:
            raise FilterError(f"unknown column {column!r}")
        if op not in OPS:
            raise FilterError(f"unknown filter op {op!r}")
        values = tuple(value.split(SEP)) if op in ("in", "between") else (value,)
        if op == "between" and len(values) != 2:
            raise FilterError("between takes two values separated by U+001F")
        if op not in NO_VALUE and not any(values):
            raise FilterError(f"filter {column}:{op} needs a value")
        out.append(Filter(column, op, values))
    return out


def like_pattern(value: str, *, prefix_only: bool) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%" if prefix_only else f"%{escaped}%"
