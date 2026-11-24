"""Decode the outer recording while leaving the original packet untouched."""

from __future__ import annotations

import json
from dataclasses import dataclass

from station import require_integer, require_name


@dataclass(frozen=True)
class RecordingEvent:
    kind: str
    slot: int | None = None
    sequence: int | None = None
    payload: bytes | None = None
    operation: str | None = None

    def fingerprint_material(self) -> bytes:
        payload = None if self.payload is None else self.payload.hex()
        return json.dumps(
            [self.kind, self.slot, self.sequence, payload, self.operation],
            separators=(',', ':'),
        ).encode('ascii')


def _parse_event(event: object) -> RecordingEvent:
    if not isinstance(event, dict):
        raise ValueError('event must be an object')
    kind = event.get('kind')
    if kind in ('issue', 'reply') and event.keys() == {'kind', 'operation'}:
        return RecordingEvent(kind, operation=require_name(event['operation'], 'operation'))

    fields = {'kind', 'slot', 'sequence'}
    if kind == 'record':
        fields.add('payload')
    if kind not in ('record', 'cover') or event.keys() != fields:
        raise ValueError('invalid event kind or fields')
    slot = require_integer(event['slot'], 'slot')
    sequence = require_integer(event['sequence'], 'sequence')
    payload = None
    if kind == 'record':
        if not isinstance(event['payload'], str):
            raise ValueError('payload must be hexadecimal text')
        payload = bytes.fromhex(event['payload'])
    return RecordingEvent(kind, slot, sequence, payload)


def parse_recording(text: str) -> tuple[RecordingEvent, ...]:
    if not isinstance(text, str):
        raise ValueError('recording must be JSON text')
    try:
        items = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid recording JSON') from error
    if not isinstance(items, list):
        raise ValueError('recording must be an array')
    events = []
    for index, item in enumerate(items):
        try:
            events.append(_parse_event(item))
        except ValueError as error:
            raise ValueError(f'recording event {index}: {error}') from error
    return tuple(events)
