"""Station-side operation registration and durable receipt storage."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredRepairRequest:
    operation: str
    shift_id: str
    device: str
    sequence: int


@dataclass(frozen=True)
class StoredReceipt:
    receipt_id: int
    device: str
    sequence: int
    slot: int
    micrometers: int
    raw: bytes
    operation: str | None


class CaptureRepository:
    def __init__(self, database: sqlite3.Connection):
        self.database = database

    def initialize(self) -> None:
        self.database.executescript('''
            CREATE TABLE IF NOT EXISTS repair_requests (
                operation TEXT PRIMARY KEY,
                shift_id TEXT NOT NULL REFERENCES shifts (shift_id),
                device TEXT NOT NULL REFERENCES station_devices (device),
                sequence INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS repair_positions
                ON repair_requests (device, sequence, operation);
            CREATE TABLE IF NOT EXISTS intake_receipts (
                receipt_id INTEGER PRIMARY KEY,
                device TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                slot INTEGER NOT NULL,
                micrometers INTEGER NOT NULL,
                raw BLOB NOT NULL,
                operation TEXT REFERENCES repair_requests (operation),
                UNIQUE (device, sequence)
            );
        ''')

    @contextmanager
    def transaction(self):
        self.database.execute('BEGIN IMMEDIATE')
        try:
            yield self.database
            self.database.commit()
        except BaseException:
            self.database.rollback()
            raise

    def get_request(self, operation: str) -> StoredRepairRequest | None:
        row = self.database.execute(
            'SELECT * FROM repair_requests WHERE operation = ?', (operation,),
        ).fetchone()
        return None if row is None else StoredRepairRequest(**dict(row))

    def insert_request(self, request: StoredRepairRequest) -> None:
        self.database.execute(
            'INSERT INTO repair_requests (operation, shift_id, device, sequence) VALUES (?, ?, ?, ?)',
            (request.operation, request.shift_id, request.device, request.sequence),
        )

    def requests_at(self, device: str, sequence: int) -> tuple[StoredRepairRequest, ...]:
        rows = self.database.execute('''
            SELECT * FROM repair_requests WHERE device = ? AND sequence = ? ORDER BY operation
        ''', (device, sequence))
        return tuple(StoredRepairRequest(**dict(row)) for row in rows)

    def get_receipt(self, device: str, sequence: int) -> StoredReceipt | None:
        row = self.database.execute(
            'SELECT * FROM intake_receipts WHERE device = ? AND sequence = ?', (device, sequence),
        ).fetchone()
        return None if row is None else StoredReceipt(**dict(row))

    def insert_receipt(
        self, device: str, sequence: int, slot: int, micrometers: int, raw: bytes, operation: str | None,
    ) -> StoredReceipt:
        result = self.database.execute('''
            INSERT INTO intake_receipts (device, sequence, slot, micrometers, raw, operation)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (device, sequence, slot, micrometers, raw, operation))
        return StoredReceipt(result.lastrowid, device, sequence, slot, micrometers, raw, operation)

    def list_receipts(self, device: str, start: int, end: int) -> tuple[StoredReceipt, ...]:
        rows = self.database.execute('''
            SELECT * FROM intake_receipts WHERE device = ? AND sequence BETWEEN ? AND ?
            ORDER BY sequence
        ''', (device, start, end))
        return tuple(StoredReceipt(**dict(row)) for row in rows)
