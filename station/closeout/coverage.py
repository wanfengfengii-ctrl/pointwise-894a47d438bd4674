"""Resolve each expected position from durable receipts and device facts."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass

from station.capture.receipts import Receipt, receipts_for_range
from station.capture.repair import repair_facts_for_position
from station.device.repository import DeviceRepository
from station.shift.repository import ShiftRecord
from station.shift.roster import iter_fixed_positions


@dataclass(frozen=True)
class PositionOutcome:
    sequence: int
    state: str
    receipt: Receipt | None = None


def resolve_coverage(
    database, shift: ShiftRecord, source: DeviceRepository | None,
) -> tuple[PositionOutcome, ...]:
    sequences = iter_fixed_positions(shift)
    if source is not None and source.device != shift.device:
        raise ValueError('closeout source is a different device')
    received = {
        receipt.packet.sequence: receipt
        for receipt in receipts_for_range(database, shift.device, shift.start, shift.end)
    }
    outcomes = []
    snapshot = nullcontext() if source is None else source.transaction(write=False)
    with snapshot:
        for sequence in sequences:
            receipt = received.get(sequence)
            if receipt is not None:
                outcomes.append(PositionOutcome(sequence, 'received', receipt))
            elif source is not None and repair_facts_for_position(database, source, sequence).conclusively_missing:
                outcomes.append(PositionOutcome(sequence, 'missing'))
            else:
                outcomes.append(PositionOutcome(sequence, 'waiting'))
    return tuple(outcomes)
