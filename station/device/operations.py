"""Original operation numbers survive the lifetime of a physical slot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from station import require_integer, require_name
from station.device.repository import DeviceRepository
from station.device.slots import (
    SlotImage, PositionAvailability, position_availability, read_origin, read_position,
)


class RepairState(str, Enum):
    UNKNOWN = 'unknown'
    PENDING = 'pending'
    NOT_ISSUED = 'not_issued'
    ISSUED = 'issued'


@dataclass(frozen=True)
class RepairOutcome:
    operation: str
    state: RepairState
    sequence: int | None = None
    image: SlotImage | None = None
    boot: str = ''

    def position(self) -> tuple[str, int] | None:
        return None if self.sequence is None else (self.boot, self.sequence)


def query_repair(repository: DeviceRepository, operation: str) -> RepairOutcome:
    require_name(operation, 'operation')
    row = repository.connection.execute(
        'SELECT boot, sequence, state, slot, raw, origin_id FROM operations WHERE operation = ?',
        (operation,),
    ).fetchone()
    if row is None:
        return RepairOutcome(operation, RepairState.UNKNOWN)
    state = RepairState(row['state'])
    image = None
    if state is RepairState.ISSUED:
        if row['origin_id'] is not None:
            image = read_origin(repository, row['origin_id'])
        if image is None:
            # Older databases stored the issued bytes on the operation row.
            image = SlotImage(
                repository.device, row['sequence'], row['slot'], row['raw'], row['boot'],
                row['origin_id'],
            )
    return RepairOutcome(operation, state, row['sequence'], image, row['boot'])


def accept_repair(
    repository: DeviceRepository, operation: str, sequence: int, boot: str | None = None,
) -> RepairOutcome:
    boot = '' if boot is None else require_name(boot, 'boot')
    require_integer(sequence, 'sequence')
    with repository.transaction() as database:
        previous = query_repair(repository, operation)
        if previous.state is not RepairState.UNKNOWN:
            if (previous.boot, previous.sequence) != (boot, sequence):
                raise ValueError('device operation already binds a different position')
            return previous
        database.execute(
            "INSERT INTO operations (operation, boot, sequence, state) VALUES (?, ?, ?, 'pending')",
            (operation, boot, sequence),
        )
        return RepairOutcome(operation, RepairState.PENDING, sequence, boot=boot)


def issue_in_transaction(repository: DeviceRepository, operation: str) -> RepairOutcome:
    database = repository.connection
    if not database.in_transaction:
        raise RuntimeError('issuing a repair requires a device transaction')
    previous = query_repair(repository, operation)
    if previous.state is not RepairState.PENDING:
        return previous

    position = (previous.boot, previous.sequence)
    image = read_position(repository, position)
    if image is not None:
        database.execute(
            'UPDATE operations SET state = ?, slot = ?, raw = ?, origin_id = ? WHERE operation = ?',
            ('issued', image.slot, image.raw, image.origin_id, operation),
        )
        return RepairOutcome(operation, RepairState.ISSUED, previous.sequence, image, previous.boot)
    if position_availability(repository, position) is PositionAvailability.COVERED:
        database.execute(
            "UPDATE operations SET state = 'not_issued' WHERE operation = ?", (operation,),
        )
        return RepairOutcome(operation, RepairState.NOT_ISSUED, previous.sequence, boot=previous.boot)
    return previous


def issue_repair(repository: DeviceRepository, operation: str) -> RepairOutcome:
    with repository.transaction():
        return issue_in_transaction(repository, operation)


def operation_origin(repository: DeviceRepository, operation: str) -> tuple[str, int] | None:
    """Return the durable position an operation is bound to, unknown -> None."""
    row = repository.connection.execute(
        'SELECT boot, sequence FROM operations WHERE operation = ?', (operation,),
    ).fetchone()
    if row is None:
        return None
    return row['boot'], row['sequence']
