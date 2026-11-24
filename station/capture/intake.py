"""Verify delivery and return a receipt only after the station commits it."""

from __future__ import annotations

from station.capture.packets import decode_packet, validate_origin
from station.capture.receipts import Receipt, record_receipt
from station.capture.repair import load_repair_context
from station.capture.repository import CaptureRepository
from station.device.repository import DeviceRepository
from station.device.slots import SlotImage


def receive(
    database, image: SlotImage, *, operation: str | None = None, source: DeviceRepository | None = None,
) -> Receipt:
    packet = decode_packet(image.raw)
    validate_origin(packet, image)
    repository = CaptureRepository(database)
    with repository.transaction():
        if operation is not None:
            load_repair_context(database, source, operation, image)
        return record_receipt(repository, packet, operation)
