"""Return the first formal receipt for a physical packet position."""

from __future__ import annotations

from dataclasses import dataclass

from station import require_integer, require_name
from station.capture.packets import OriginalPacket
from station.capture.repository import CaptureRepository, StoredReceipt


@dataclass(frozen=True)
class Receipt:
    receipt_id: int
    packet: OriginalPacket
    operation: str | None = None


def _receipt(row: StoredReceipt) -> Receipt:
    packet = OriginalPacket(row.device, row.sequence, row.slot, row.micrometers, row.raw)
    return Receipt(row.receipt_id, packet, row.operation)


def record_receipt(repository: CaptureRepository, packet: OriginalPacket, operation: str | None) -> Receipt:
    previous = repository.get_receipt(packet.device, packet.sequence)
    if previous is not None:
        existing = _receipt(previous)
        if existing.packet != packet:
            raise ValueError('a different original was already received at this position')
        return existing
    stored = repository.insert_receipt(
        packet.device, packet.sequence, packet.slot, packet.micrometers, packet.raw, operation,
    )
    return _receipt(stored)


def receipts_for_range(database, device: str, start: int, end: int) -> tuple[Receipt, ...]:
    require_name(device, 'device')
    require_integer(start, 'range start')
    require_integer(end, 'range end', start)
    return tuple(_receipt(row) for row in CaptureRepository(database).list_receipts(device, start, end))
