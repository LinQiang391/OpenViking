# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared event ranking contract for SQL-backed retrieval."""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from numbers import Real
from typing import Any, Optional

EVENT_TIME_DECAY_SCALE = "7d"
EVENT_TIME_DECAY_DECAY = 0.5
MAX_DURATION_MS = 3000 * 365 * 24 * 60 * 60 * 1000


def parse_duration_ms(value: Any, *, parameter_name: str = "duration") -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0|[0-9]+[mhd]", value):
        raise ValueError(f"{parameter_name} must be '0' or a non-negative integer with m/h/d")
    if value == "0":
        return 0
    if len(value[:-1]) > 16:
        raise ValueError(f"{parameter_name} exceeds the maximum duration")
    result = int(value[:-1]) * {"m": 60000, "h": 3600000, "d": 86400000}[value[-1]]
    if result > MAX_DURATION_MS:
        raise ValueError(f"{parameter_name} exceeds the maximum duration")
    return result


def validate_event_time_decay_request(protection: Any) -> None:
    if protection is not None:
        parse_duration_ms(protection, parameter_name="events_time_decay_protection")


def _datetime_to_epoch_ms(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("time value must not be boolean")
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        value = value.timestamp() * 1000
    if not isinstance(value, Real):
        raise ValueError("time value must be epoch milliseconds or an ISO timestamp")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("time value must be finite")
    return result


@dataclass(frozen=True)
class TimeDecayFusionSpec:
    field: str
    origin_ms: float
    offset_ms: int
    scale_ms: int
    decay: float

    def __post_init__(self):
        if self.scale_ms <= 0 or self.offset_ms < 0 or not 0 < self.decay < 1:
            raise ValueError("invalid time decay parameters")
        object.__setattr__(self, "origin_ms", _datetime_to_epoch_ms(self.origin_ms))


def build_time_decay_fusion_spec(
    *, protection: str, origin: Optional[datetime] = None, field: str = "updated_at",
) -> TimeDecayFusionSpec:
    return TimeDecayFusionSpec(
        field=field,
        origin_ms=_datetime_to_epoch_ms(origin if origin is not None else datetime.now(timezone.utc)),
        offset_ms=parse_duration_ms(protection, parameter_name="events_time_decay_protection"),
        scale_ms=parse_duration_ms(EVENT_TIME_DECAY_SCALE),
        decay=EVENT_TIME_DECAY_DECAY,
    )


def time_decay_factor(value: Any, spec: TimeDecayFusionSpec) -> float:
    if value is None:
        return 1.0
    distance = max(0.0, abs(spec.origin_ms - _datetime_to_epoch_ms(value)) - spec.offset_ms)
    return math.exp(math.log(spec.decay) * distance / spec.scale_ms)
