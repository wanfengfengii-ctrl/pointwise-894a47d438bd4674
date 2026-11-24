"""Original packet wire format and origin validation."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from station import MIN_INTEGER, require_integer, require_name
from station.device.slots import SlotImage


@dataclass(frozen=True)
class OriginalPacket:
    device: str
    sequence: int
    slot: int
    micrometers: int
    raw: bytes
    boot: str = ''


def _validate_fields(device, sequence, slot, micrometers) -> None:
    require_name(device, 'packet device')
    require_integer(sequence, 'packet sequence')
    require_integer(slot, 'packet slot')
    require_integer(micrometers, 'micrometers', MIN_INTEGER)


def encode_packet(device: str, sequence: int, slot: int, micrometers: int) -> bytes:
    _validate_fields(device, sequence, slot, micrometers)
    body = json.dumps([device, sequence, slot, micrometers], separators=(',', ':')).encode('utf-8')
    return body + b'\n' + hashlib.sha256(body).hexdigest().encode('ascii')


def decode_packet(raw: bytes) -> OriginalPacket:
    if not isinstance(raw, bytes):
        raise ValueError('original packet must be bytes')
    body, separator, checksum = raw.rpartition(b'\n')
    if not separator or not hmac.compare_digest(checksum, hashlib.sha256(body).hexdigest().encode('ascii')):
        raise ValueError('bad packet checksum')
    try:
        fields = json.loads(body.decode('utf-8'))
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid packet body') from error
    if not isinstance(fields, list) or len(fields) != 4:
        raise ValueError('invalid packet fields')
    _validate_fields(*fields)
    return OriginalPacket(*fields, raw)


def validate_origin(packet: OriginalPacket, image: SlotImage) -> None:
    require_name(image.device, 'origin device')
    require_integer(image.sequence, 'origin sequence')
    require_integer(image.slot, 'origin slot')
    if image.boot:
        require_name(image.boot, 'origin boot')
    if (packet.device, packet.sequence, packet.slot) != (image.device, image.sequence, image.slot):
        raise ValueError('packet position does not match delivery origin')
