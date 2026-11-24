"""Physical slot changes leave durable evidence about old source positions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from station import require_integer
from station.device.repository import DeviceRepository


@dataclass(frozen=True)
class SlotImage:
    device: str
    sequence: int
    slot: int
    raw: bytes


class PositionAvailability(str, Enum):
    READABLE = 'readable'
    COVERED = 'covered'
    UNOBSERVED = 'unobserved'


def read_position(repository: DeviceRepository, sequence: int) -> SlotImage | None:
    row = repository.connection.execute(
        'SELECT slot, raw FROM slots WHERE sequence = ?', (sequence,),
    ).fetchone()
    if row is None:
        return None
    return SlotImage(repository.device, sequence, row['slot'], row['raw'])


def position_availability(repository: DeviceRepository, sequence: int) -> PositionAvailability:
    row = repository.connection.execute('''
        SELECT CASE
            WHEN EXISTS (SELECT 1 FROM covered WHERE sequence = ?) THEN 'covered'
            WHEN EXISTS (SELECT 1 FROM slots WHERE sequence = ?) THEN 'readable'
            ELSE 'unobserved'
        END AS availability
    ''', (sequence, sequence)).fetchone()
    return PositionAvailability(row['availability'])


def _check_write(repository: DeviceRepository, slot: int) -> None:
    if not repository.connection.in_transaction:
        raise RuntimeError('slot changes require a device transaction')
    if require_integer(slot, 'slot') >= repository.slot_count:
        raise ValueError('slot is outside recorder capacity')


def _cover(repository: DeviceRepository, slot: int, sequence: int) -> int:
    database = repository.connection
    database.execute('INSERT INTO covered (sequence) VALUES (?)', (sequence,))
    database.execute('DELETE FROM slots WHERE slot = ?', (slot,))
    return sequence


def cover_position(repository: DeviceRepository, slot: int, sequence: int) -> int:
    _check_write(repository, slot)
    image = read_position(repository, sequence)
    if image is None or image.slot != slot:
        raise ValueError('cover event does not match the current slot position')
    return _cover(repository, slot, sequence)


def place_packet(repository: DeviceRepository, image: SlotImage) -> int | None:
    _check_write(repository, image.slot)
    require_integer(image.sequence, 'sequence')
    if image.device != repository.device or not isinstance(image.raw, bytes):
        raise ValueError('slot image has the wrong device or payload type')
    if position_availability(repository, image.sequence) is not PositionAvailability.UNOBSERVED:
        raise ValueError('recording repeats an existing source position')

    database = repository.connection
    old = database.execute('SELECT sequence FROM slots WHERE slot = ?', (image.slot,)).fetchone()
    covered = None if old is None else _cover(repository, image.slot, old['sequence'])
    database.execute(
        'INSERT INTO slots (slot, sequence, raw) VALUES (?, ?, ?)',
        (image.slot, image.sequence, image.raw),
    )
    return covered
