"""Original operation numbers survive the lifetime of a physical slot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from station import require_integer, require_name
from station.device.repository import DeviceRepository
from station.device.slots import SlotImage, PositionAvailability, position_availability, read_position


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


def query_repair(repository: DeviceRepository, operation: str) -> RepairOutcome:
    require_name(operation, 'operation')
    row = repository.connection.execute(
        'SELECT sequence, state, slot, raw FROM operations WHERE operation = ?', (operation,),
    ).fetchone()
    if row is None:
        return RepairOutcome(operation, RepairState.UNKNOWN)
    state = RepairState(row['state'])
    image = None
    if state is RepairState.ISSUED:
        image = SlotImage(repository.device, row['sequence'], row['slot'], row['raw'])
    return RepairOutcome(operation, state, row['sequence'], image)


def accept_repair(repository: DeviceRepository, operation: str, sequence: int) -> RepairOutcome:
    require_integer(sequence, 'sequence')
    with repository.transaction() as database:
        previous = query_repair(repository, operation)
        if previous.state is not RepairState.UNKNOWN:
            if previous.sequence != sequence:
                raise ValueError('device operation already binds a different position')
            return previous
        database.execute(
            "INSERT INTO operations (operation, sequence, state) VALUES (?, ?, 'pending')",
            (operation, sequence),
        )
        return RepairOutcome(operation, RepairState.PENDING, sequence)


def issue_in_transaction(repository: DeviceRepository, operation: str) -> RepairOutcome:
    database = repository.connection
    if not database.in_transaction:
        raise RuntimeError('issuing a repair requires a device transaction')
    previous = query_repair(repository, operation)
    if previous.state is not RepairState.PENDING:
        return previous

    sequence = previous.sequence
    image = read_position(repository, sequence)
    if image is not None:
        database.execute(
            "UPDATE operations SET state = 'issued', slot = ?, raw = ? WHERE operation = ?",
            (image.slot, image.raw, operation),
        )
        return RepairOutcome(operation, RepairState.ISSUED, sequence, image)
    if position_availability(repository, sequence) is PositionAvailability.COVERED:
        database.execute(
            "UPDATE operations SET state = 'not_issued' WHERE operation = ?", (operation,),
        )
        return RepairOutcome(operation, RepairState.NOT_ISSUED, sequence)
    return previous


def issue_repair(repository: DeviceRepository, operation: str) -> RepairOutcome:
    with repository.transaction():
        return issue_in_transaction(repository, operation)
