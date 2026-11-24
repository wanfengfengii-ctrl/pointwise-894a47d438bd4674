"""Registration fixes the device and the first expected source position."""

from station import require_integer, require_name
from station.capture.repository import CaptureRepository
from station.shift.repository import ShiftRecord, ShiftRepository


def open_shift(database, shift_id: str, device: str, start: int) -> ShiftRecord:
    require_name(shift_id, 'shift id')
    require_name(device, 'device')
    require_integer(start, 'start')
    repository = ShiftRepository(database)
    with CaptureRepository(database).transaction():
        previous = repository.get_shift(shift_id)
        if previous is not None:
            if (previous.device, previous.start) != (device, start):
                raise ValueError('shift id already binds a different device or first position')
            return previous
        shift = ShiftRecord(shift_id, device, start)
        repository.insert_shift(shift)
        return shift
