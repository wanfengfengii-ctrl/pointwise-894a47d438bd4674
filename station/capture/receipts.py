"""Return the first formal receipt for a physical packet position."""

from __future__ import annotations

from dataclasses import dataclass, replace

from station import require_integer, require_name
from station.capture.packets import OriginalPacket
from station.capture.repository import CaptureRepository, StoredReceipt
from station.positions import LEGACY_BOOT


@dataclass(frozen=True)
class Receipt:
    receipt_id: int
    packet: OriginalPacket
    operation: str | None = None

    @property
    def boot(self) -> str:
        return self.packet.boot

    @property
    def position(self) -> int | tuple[str, int]:
        return self.packet.sequence if self.boot == LEGACY_BOOT else (self.boot, self.packet.sequence)


def _receipt(row: StoredReceipt) -> Receipt:
    packet = OriginalPacket(
        row.device, row.sequence, row.slot, row.micrometers, row.raw, row.boot,
    )
    return Receipt(row.receipt_id, packet, row.operation)


def record_receipt(
    repository: CaptureRepository, packet: OriginalPacket, operation: str | None, boot: str = '',
) -> Receipt:
    packet = replace(packet, boot=boot)
    previous = repository.get_receipt(packet.device, boot, packet.sequence)
    if previous is not None:
        existing = _receipt(previous)
        if (existing.packet.device, existing.packet.sequence, existing.packet.slot,
                existing.packet.micrometers, existing.packet.raw) != (
                packet.device, packet.sequence, packet.slot, packet.micrometers, packet.raw):
            raise ValueError('a different original was already received at this position')
        return existing
    stored = repository.insert_receipt(
        packet.device, boot, packet.sequence, packet.slot, packet.micrometers, packet.raw, operation,
    )
    return _receipt(stored)


def receipts_for_range(
    database, device: str, start: int, end: int, boot: str = LEGACY_BOOT,
) -> tuple[Receipt, ...]:
    require_name(device, 'device')
    require_integer(start, 'range start')
    require_integer(end, 'range end', start)
    return tuple(
        _receipt(row) for row in CaptureRepository(database).list_receipts(device, boot, start, end)
    )
