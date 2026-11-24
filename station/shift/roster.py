"""Interpret station shift records consistently across repair and publication."""

from __future__ import annotations

from station import require_name
from station.shift.repository import ShiftRecord, ShiftRepository


def lookup_shift(database, shift_id: str) -> ShiftRecord:
    require_name(shift_id, 'shift id')
    shift = ShiftRepository(database).get_shift(shift_id)
    if shift is None:
        raise KeyError(shift_id)
    return shift


def contains_key(shift: ShiftRecord, device: str, boot: str, sequence: int) -> bool:
    """Membership test for a durable (boot key, sequence) identity."""
    if shift.device != device:
        return False
    if shift.segments is not None:
        return shift.positions(boot, sequence)
    return sequence >= shift.start and (shift.end is None or sequence <= shift.end)


def iter_fixed_positions(shift: ShiftRecord) -> tuple[tuple[str, int], ...]:
    """All fixed (boot_key, sequence) positions in declaration order."""
    return shift.ordered_positions()
