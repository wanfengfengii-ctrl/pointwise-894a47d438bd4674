"""Physical slot changes leave durable evidence about old source positions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from station import require_integer, require_name
from station.device.repository import DeviceRepository
from station.positions import key_position


@dataclass(frozen=True)
class SlotImage:
    device: str
    sequence: int
    slot: int
    raw: bytes
    boot: str = ''
    origin_id: int | None = field(default=None, compare=False)


class PositionAvailability(str, Enum):
    READABLE = 'readable'
    COVERED = 'covered'
    UNOBSERVED = 'unobserved'


def read_position(repository: DeviceRepository, position: object) -> SlotImage | None:
    boot, sequence = key_position(position)
    row = repository.connection.execute(
        'SELECT boot, slot, raw, origin_id FROM slots WHERE boot = ? AND sequence = ?',
        (boot, sequence),
    ).fetchone()
    if row is None:
        return None
    return SlotImage(repository.device, sequence, row['slot'], row['raw'], row['boot'], row['origin_id'])


def read_origin(repository: DeviceRepository, origin_id: int) -> SlotImage | None:
    row = repository.connection.execute(
        'SELECT boot, sequence, slot, raw FROM origins WHERE origin_id = ?', (origin_id,),
    ).fetchone()
    if row is None:
        return None
    return SlotImage(repository.device, row['sequence'], row['slot'], row['raw'], row['boot'], origin_id)


def position_availability(repository: DeviceRepository, position: object) -> PositionAvailability:
    boot, sequence = key_position(position)
    row = repository.connection.execute('''
        SELECT CASE
            WHEN EXISTS (SELECT 1 FROM covered WHERE boot = ? AND sequence = ?) THEN 'covered'
            WHEN EXISTS (SELECT 1 FROM slots WHERE boot = ? AND sequence = ?) THEN 'readable'
            ELSE 'unobserved'
        END AS availability
    ''', (boot, sequence, boot, sequence)).fetchone()
    return PositionAvailability(row['availability'])


def validate_delivery(repository: DeviceRepository, image: SlotImage) -> None:
    """Require the delivered bytes to be the recorder's own capture at that start."""
    if not isinstance(image, SlotImage) or image.device != repository.device:
        raise ValueError('delivery does not come from this recorder')
    require_integer(image.sequence, 'sequence')
    require_integer(image.slot, 'slot')
    if not isinstance(image.raw, bytes):
        raise ValueError('delivery payload must be bytes')
    boot = require_name(image.boot, 'boot') if image.boot else ''
    if image.origin_id is not None:
        origin = read_origin(repository, image.origin_id)
        if origin is None or (origin.boot, origin.sequence, origin.slot, origin.raw) != (
            boot, image.sequence, image.slot, image.raw,
        ):
            raise ValueError('delivery origin does not match this start')
        return
    if boot:
        # A start-bound delivery must carry the recorder's own origin row; the
        # identical bytes of another start are a different original.
        raise ValueError('delivery origin does not match this start')
    # Legacy callers hand over only the four-field image; accept it when the
    # durable origin row is byte-for-byte the same legacy capture.
    row = repository.connection.execute(
        'SELECT 1 FROM origins WHERE boot = ? AND sequence = ? AND slot = ? AND raw = ?',
        (boot, image.sequence, image.slot, image.raw),
    ).fetchone()
    if row is None:
        raise ValueError('delivery origin does not match this start')


def _check_write(repository: DeviceRepository, slot: int) -> None:
    if not repository.connection.in_transaction:
        raise RuntimeError('slot changes require a device transaction')
    if require_integer(slot, 'slot') >= repository.slot_count:
        raise ValueError('slot is outside recorder capacity')


def _cover(repository: DeviceRepository, slot: int, boot: str, sequence: int) -> int:
    database = repository.connection
    database.execute('INSERT INTO covered (boot, sequence) VALUES (?, ?)', (boot, sequence))
    database.execute('DELETE FROM slots WHERE slot = ?', (slot,))
    return sequence


def cover_position(
    repository: DeviceRepository, slot: int, sequence: int, boot: str | None = None,
) -> int:
    _check_write(repository, slot)
    database = repository.connection
    row = database.execute(
        'SELECT boot, sequence FROM slots WHERE slot = ?', (slot,),
    ).fetchone()
    if row is None or row['sequence'] != sequence:
        raise ValueError('cover event does not match the current slot position')
    actual_boot = row['boot']
    if boot is not None and require_name(boot, 'boot') != actual_boot:
        raise ValueError('cover event does not match the current slot position')
    return _cover(repository, slot, actual_boot, sequence)


def place_packet(repository: DeviceRepository, image: SlotImage) -> int | None:
    _check_write(repository, image.slot)
    require_integer(image.sequence, 'sequence')
    if image.device != repository.device or not isinstance(image.raw, bytes):
        raise ValueError('slot image has the wrong device or payload type')
    boot = image.boot
    if boot:
        boot = require_name(boot, 'boot')
    if boot != repository.current_boot():
        raise ValueError('record does not belong to the active start')
    if position_availability(repository, (boot, image.sequence)) is not PositionAvailability.UNOBSERVED:
        raise ValueError('recording repeats an existing source position')

    database = repository.connection
    origin = database.execute(
        'INSERT INTO origins (boot, sequence, slot, raw) VALUES (?, ?, ?, ?)',
        (boot, image.sequence, image.slot, image.raw),
    )
    origin_id = origin.lastrowid
    row = database.execute('SELECT boot, sequence FROM slots WHERE slot = ?', (image.slot,)).fetchone()
    covered = None
    if row is not None:
        covered = _cover(repository, image.slot, row['boot'], row['sequence'])
    database.execute(
        'INSERT INTO slots (slot, boot, sequence, raw, origin_id) VALUES (?, ?, ?, ?, ?)',
        (image.slot, boot, image.sequence, image.raw, origin_id),
    )
    return covered
