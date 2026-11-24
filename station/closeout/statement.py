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


def encode_statement(statement: Statement) -> str:
    document = asdict(statement)
    for position in document['positions']:
        receipt = position['receipt']
        if receipt is not None:
            packet = receipt['packet']
            packet['raw'] = packet['raw'].hex()
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
            receipt = Receipt(stored['receipt_id'], OriginalPacket(**fields), stored['operation'])
        positions.append(PositionOutcome(row['sequence'], row['state'], receipt))
    stats = document['stats']
    stats['missing'] = tuple(stats['missing'])
    return Statement(
        document['result_id'], ShiftRecord(**document['shift']), tuple(positions), MeasurementStats(**stats),
    )
