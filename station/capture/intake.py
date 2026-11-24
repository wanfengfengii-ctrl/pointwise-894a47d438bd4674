"""Verify delivery and return a receipt only after the station commits it."""

from __future__ import annotations

from station.capture.packets import decode_packet, validate_origin
from station.capture.receipts import Receipt, record_receipt
from station.capture.repair import load_repair_context
from station.capture.repository import CaptureRepository
from station.device.repository import DeviceRepository
from station.device.slots import SlotImage, validate_delivery
from station.positions import key_boot


def receive(
    database, image: SlotImage, *, operation: str | None = None, source: DeviceRepository | None = None,
) -> Receipt:
    if not isinstance(image, SlotImage):
        raise ValueError('delivery must be a slot image')
    packet = decode_packet(image.raw)
    validate_origin(packet, image)
    boot = key_boot(image.boot)
    if source is None and boot:
        raise ValueError('a boot delivery requires its recorder source')
    repository = CaptureRepository(database)
    with repository.transaction():
        if operation is not None:
            # Repair replies are checked against the issued original first so
            # a swapped body keeps its original diagnostic.
            load_repair_context(database, source, operation, image)
        if source is not None:
            validate_delivery(source, image)
        return record_receipt(repository, packet, operation, boot)
