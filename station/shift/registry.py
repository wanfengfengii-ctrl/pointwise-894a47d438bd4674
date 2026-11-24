"""Registration fixes the device and the expected start segments."""

from __future__ import annotations

from station import require_integer, require_name
from station.capture.repository import CaptureRepository
from station.shift.repository import ShiftRecord, ShiftRepository


def _parse_segments(segments: object) -> tuple[tuple[str, int, int], ...]:
    if isinstance(segments, (str, bytes)) or not isinstance(segments, (tuple, list)):
        raise ValueError('shift start must be an integer or a sequence of segments')
    parsed = []
    seen = set()
    for item in segments:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise ValueError('each segment must be a (boot, first, last) triple')
        boot, first, last = item
        boot = require_name(boot, 'boot')
        first = require_integer(first, 'segment first position')
        last = require_integer(last, 'segment last position', first)
        if boot in seen:
            raise ValueError('each boot may appear once in a shift')
        seen.add(boot)
        parsed.append((boot, first, last))
    if not parsed:
        raise ValueError('a shift needs at least one start segment')
    return tuple(parsed)


def open_shift(database, shift_id: str, device: str, start) -> ShiftRecord:
    require_name(shift_id, 'shift id')
    require_name(device, 'device')
    repository = ShiftRepository(database)
    if type(start) is int:
        require_integer(start, 'start')
        candidate = ShiftRecord(shift_id, device, start)
    else:
        segments = _parse_segments(start)
        candidate = ShiftRecord(shift_id, device, segments[0][1], segments[-1][2], segments)
    with CaptureRepository(database).transaction():
        previous = repository.get_shift(shift_id)
        if previous is not None:
            if (previous.device, previous.start, previous.end, previous.segments) != (
                candidate.device, candidate.start, candidate.end, candidate.segments,
            ):
                raise ValueError('shift id already binds a different device or positions')
            return previous
        repository.insert_shift(candidate)
        return candidate
