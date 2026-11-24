"""Resolve each expected position from durable receipts and device facts."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass

from station.capture.receipts import Receipt, _receipt
from station.capture.repair import repair_facts_for_position
from station.capture.repository import CaptureRepository
from station.device.repository import DeviceRepository
from station.positions import LEGACY_BOOT
from station.shift.repository import ShiftRecord
from station.shift.roster import iter_fixed_positions


@dataclass(frozen=True)
class PositionOutcome:
    boot: str
    sequence: int
    state: str
    receipt: Receipt | None = None

    @property
    def position(self) -> int | tuple[str, int]:
        return self.sequence if self.boot == LEGACY_BOOT else (self.boot, self.sequence)

    @property
    def key(self) -> tuple[str, int]:
        return self.boot, self.sequence


def _received_positions(database, shift: ShiftRecord) -> dict[tuple[str, int], Receipt]:
    received = {}
    repository = CaptureRepository(database)
    for boot, first, last in shift.segment_map():
        for receipt in (
            _receipt(row) for row in repository.list_receipts(shift.device, boot, first, last)
        ):
            received[(boot, receipt.packet.sequence)] = receipt
    return received


def resolve_coverage(
    database, shift: ShiftRecord, source: DeviceRepository | None,
) -> tuple[PositionOutcome, ...]:
    positions = iter_fixed_positions(shift)
    if source is not None and source.device != shift.device:
        raise ValueError('closeout source is a different device')
    received = _received_positions(database, shift)
    outcomes = []
    snapshot = nullcontext() if source is None else source.transaction(write=False)
    with snapshot:
        for boot, sequence in positions:
            receipt = received.get((boot, sequence))
            if receipt is not None:
                outcomes.append(PositionOutcome(boot, sequence, 'received', receipt))
            elif source is not None and repair_facts_for_position(
                database, source, boot, sequence,
            ).conclusively_missing:
                outcomes.append(PositionOutcome(boot, sequence, 'missing'))
            else:
                outcomes.append(PositionOutcome(boot, sequence, 'waiting'))
    return tuple(outcomes)
