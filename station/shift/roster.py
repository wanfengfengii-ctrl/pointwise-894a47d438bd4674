"""Interpret station shift records consistently across repair and publication."""

from station import require_name
from station.shift.repository import ShiftRecord, ShiftRepository


def lookup_shift(database, shift_id: str) -> ShiftRecord:
    require_name(shift_id, 'shift id')
    shift = ShiftRepository(database).get_shift(shift_id)
    if shift is None:
        raise KeyError(shift_id)
    return shift


def contains_position(shift: ShiftRecord, device: str, sequence: int) -> bool:
    return (
        shift.device == device
        and sequence >= shift.start
        and (shift.end is None or sequence <= shift.end)
    )


def iter_fixed_positions(shift: ShiftRecord) -> range:
    if shift.end is None:
        raise ValueError('fix the shift bounds before publication')
    return range(shift.start, shift.end + 1)
