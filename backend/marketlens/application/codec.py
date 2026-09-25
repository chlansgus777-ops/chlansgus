"""Generic, type-driven JSON codec for frozen domain dataclasses.

Used to store point-in-time analysis snapshots and to decode them for historical replay.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping, Union, get_args, get_origin, get_type_hints

_HINTS: dict[type, dict[str, Any]] = {}


def encode(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            raise ValueError("naive datetime in snapshot")
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: encode(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k if not isinstance(k, Enum) else k.value): encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [encode(x) for x in obj]
    raise TypeError(f"cannot encode {type(obj)!r}")


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINTS:
        _HINTS[cls] = get_type_hints(cls)
    return _HINTS[cls]


def decode(tp: Any, data: Any) -> Any:
    if tp is Any:
        return data
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if data is None:
            return None
        last: Exception | None = None
        for a in args:
            try:
                return decode(a, data)
            except (TypeError, ValueError, KeyError) as e:
                last = e
        raise TypeError(f"cannot decode {data!r} as {tp}: {last}")
    if data is None:
        return None
    if origin in (tuple,):
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(decode(args[0], x) for x in data)
        return tuple(decode(a, x) for a, x in zip(args, data))
    if origin in (list,):
        (a,) = get_args(tp)
        return [decode(a, x) for x in data]
    if origin in (dict, Mapping, typing.Mapping) or (origin is not None and isinstance(origin, type) and issubclass(origin, Mapping)):
        kt, vt = get_args(tp)
        return {decode(kt, k): decode(vt, v) for k, v in data.items()}
    if isinstance(tp, type):
        if issubclass(tp, Enum):
            return tp(data)
        if tp is datetime:
            return datetime.fromisoformat(data)
        if tp is date:
            return date.fromisoformat(data)
        if tp is float:
            return float(data)
        if tp is int:
            if isinstance(data, float) and not data.is_integer():
                raise ValueError("expected int")
            return int(data)
        if tp in (str, bool):
            return tp(data)
        if dataclasses.is_dataclass(tp):
            hints = _hints(tp)
            kwargs = {}
            for f in dataclasses.fields(tp):
                if f.name in data:
                    kwargs[f.name] = decode(hints[f.name], data[f.name])
            return tp(**kwargs)
    if tp is int and isinstance(data, int):
        return data
    raise TypeError(f"unsupported type {tp!r}")
