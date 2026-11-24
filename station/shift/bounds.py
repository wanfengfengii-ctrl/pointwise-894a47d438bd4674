"""Fix the inclusive end of an existing shift exactly once."""

from dataclasses import replace

from station import require_integer
from station.capture.repository import CaptureRepository
from station.shift.repository import ShiftRecord, ShiftRepository
from station.shift.roster import lookup_shift


def fix_bounds(database, shift_id: str, end: int) -> ShiftRecord:
    require_integer(end, 'end')
    with CaptureRepository(database).transaction():
        shift = lookup_shift(database, shift_id)
        if end < shift.start:
            raise ValueError('shift end precedes its start')
        if shift.end is not None:
            if shift.end != end:
                raise ValueError('shift end differs from the fixed boundary')
            return shift
        ShiftRepository(database).set_end(shift_id, end)
        return replace(shift, end=end)
