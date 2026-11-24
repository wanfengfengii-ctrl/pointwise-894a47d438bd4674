"""Frozen statement snapshots include original receipts and packet bytes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from station.capture.packets import OriginalPacket
from station.capture.receipts import Receipt
from station.closeout.coverage import PositionOutcome
from station.closeout.statistics import MeasurementStats
from station.shift.repository import ShiftRecord


@dataclass(frozen=True)
class Statement:
    result_id: str
    shift: ShiftRecord
    positions: tuple[PositionOutcome, ...]
    stats: MeasurementStats


@dataclass(frozen=True)
class FirstReceipt:
    result_id: str
    receipt_id: str


@dataclass(frozen=True)
class Publication:
    statement: Statement
    first_receipt: FirstReceipt


def assemble_statement(
    shift: ShiftRecord, positions: tuple[PositionOutcome, ...], stats: MeasurementStats,
) -> Publication:
    result_id = f'shift/{shift.shift_id}'
    statement = Statement(result_id, shift, positions, stats)
    return Publication(statement, FirstReceipt(result_id, f'first/{result_id}'))


def _encode_position(key: tuple[str, int]) -> object:
    return key[1] if key[0] == '' else [key[0], key[1]]


def _decode_position(key: object) -> tuple[str, int]:
    if isinstance(key, list):
        return key[0], key[1]
    return '', key


def encode_statement(statement: Statement) -> str:
    document = asdict(statement)
    document['positions'] = [
        {
            'boot': position.boot,
            'sequence': position.sequence,
            'state': position.state,
            'receipt': None if position.receipt is None else {
                'receipt_id': position.receipt.receipt_id,
                'operation': position.receipt.operation,
                'boot': position.receipt.boot,
                'packet': {
                    'device': position.receipt.packet.device,
                    'sequence': position.receipt.packet.sequence,
                    'slot': position.receipt.packet.slot,
                    'micrometers': position.receipt.packet.micrometers,
                    'raw': position.receipt.packet.raw.hex(),
                },
            },
        }
        for position in statement.positions
    ]
    document['stats']['missing'] = [
        _encode_position(_decode_position(key)) for key in statement.stats.missing
    ]
    return json.dumps(document, separators=(',', ':'), sort_keys=True)


def decode_statement(text: str) -> Statement:
    document = json.loads(text)
    positions = []
    for row in document['positions']:
        stored = row['receipt']
        receipt = None
        if stored is not None:
            fields = stored['packet']
            fields['raw'] = bytes.fromhex(fields['raw'])
            fields['boot'] = stored.get('boot', fields.get('boot', ''))
            receipt = Receipt(stored['receipt_id'], OriginalPacket(**fields), stored['operation'])
        positions.append(PositionOutcome(row.get('boot', ''), row['sequence'], row['state'], receipt))
    stats = document['stats']
    stats['missing'] = tuple(
        sequence if boot == '' else (boot, sequence)
        for boot, sequence in (_decode_position(key) for key in stats['missing'])
    )
    shift_data = dict(document['shift'])
    segments = shift_data.get('segments')
    if segments is not None and not isinstance(segments, tuple):
        shift_data['segments'] = tuple(tuple(segment) for segment in segments)
    shift = ShiftRecord(**shift_data)
    return Statement(document['result_id'], shift, tuple(positions), MeasurementStats(**stats))
