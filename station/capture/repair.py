"""Match station registrations to the recorder's authoritative outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from station import require_name
from station.capture.repository import CaptureRepository, StoredRepairRequest
from station.device.operations import RepairOutcome, RepairState, accept_repair, query_repair
from station.device.repository import DeviceRepository
from station.device.slots import PositionAvailability, SlotImage, position_availability
from station.positions import key_boot, position_key
from station.shift.roster import contains_key, lookup_shift


@dataclass(frozen=True)
class PositionRepairFacts:
    availability: PositionAvailability
    outcomes: tuple[RepairOutcome, ...]

    @property
    def conclusively_missing(self) -> bool:
        return self.availability is PositionAvailability.COVERED and all(
            outcome.state is RepairState.NOT_ISSUED for outcome in self.outcomes
        )

    def issued_image(self, operation: str) -> SlotImage | None:
        for outcome in self.outcomes:
            if outcome.operation == operation and outcome.state is RepairState.ISSUED:
                return outcome.image
        return None


def request_repair(
    database, shift_id: str, device: str, position, operation: str,
    *, source: DeviceRepository | None = None,
) -> StoredRepairRequest:
    require_name(shift_id, 'shift id')
    require_name(device, 'device')
    require_name(operation, 'operation')
    boot, sequence = position_key(position, 'repair position')
    if source is not None and source.device != device:
        raise ValueError('repair source is a different device')

    repository = CaptureRepository(database)
    request = StoredRepairRequest(operation, shift_id, device, boot, sequence)
    with repository.transaction():
        previous = repository.get_request(operation)
        if previous is None:
            shift = lookup_shift(database, shift_id)
            if not contains_key(shift, device, boot, sequence):
                raise ValueError('repair position is outside its shift')
            repository.insert_request(request)
        elif previous != request:
            raise ValueError('repair operation already binds a different request')

    if source is not None:
        accept_repair(source, operation, sequence, None if boot == '' else boot)
    return request


def repair_facts_for_position(
    database, source: DeviceRepository, boot: str, sequence: int,
) -> PositionRepairFacts:
    """Read under the calling station transaction and a device read snapshot."""
    availability = position_availability(source, (boot, sequence))
    requests = CaptureRepository(database).requests_at(source.device, boot, sequence)
    outcomes = []
    for request in requests:
        outcome = query_repair(source, request.operation)
        if outcome.sequence is not None and (outcome.boot, outcome.sequence) != (boot, sequence):
            raise ValueError('device outcome conflicts with its station request')
        outcomes.append(outcome)
    return PositionRepairFacts(availability, tuple(outcomes))


def load_repair_context(
    database, source: DeviceRepository | None, operation: str, image: SlotImage,
) -> StoredRepairRequest:
    require_name(operation, 'operation')
    boot = key_boot(image.boot)
    request = CaptureRepository(database).get_request(operation)
    if request is None or (request.device, request.boot, request.sequence) != (
        image.device, boot, image.sequence,
    ):
        raise ValueError('repair reply does not match a registered request')
    if source is None or source.device != request.device:
        raise ValueError('repair reply requires its source device')
    with source.transaction(write=False):
        facts = repair_facts_for_position(database, source, boot, image.sequence)
        if facts.issued_image(operation) != image:
            raise ValueError('repair reply does not match the issued original')
    return request
