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
    boot: str
    sequence: int


@dataclass(frozen=True)
class StoredReceipt:
    receipt_id: int
    device: str
    boot: str
    sequence: int
    slot: int
    micrometers: int
    raw: bytes
    operation: str | None


class CaptureRepository:
    def __init__(self, database: sqlite3.Connection):
        self.database = database

    def initialize(self) -> None:
        self._upgrade_legacy_schema()
        self.database.executescript('''
            CREATE TABLE IF NOT EXISTS repair_requests (
                operation TEXT PRIMARY KEY,
                shift_id TEXT NOT NULL REFERENCES shifts (shift_id),
                device TEXT NOT NULL REFERENCES station_devices (device),
                boot TEXT NOT NULL,
                sequence INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS repair_positions
                ON repair_requests (device, boot, sequence, operation);
            CREATE TABLE IF NOT EXISTS intake_receipts (
                receipt_id INTEGER PRIMARY KEY,
                device TEXT NOT NULL,
                boot TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                slot INTEGER NOT NULL,
                micrometers INTEGER NOT NULL,
                raw BLOB NOT NULL,
                operation TEXT REFERENCES repair_requests (operation),
                UNIQUE (device, boot, sequence)
            );
        ''')

    def _upgrade_legacy_schema(self) -> None:
        """Keep pre-restart databases usable while dropping sequence-only identity."""
        requests = self._columns('repair_requests')
        if requests and 'boot' not in requests:
            self.database.executescript('''
                ALTER TABLE repair_requests ADD COLUMN boot TEXT NOT NULL DEFAULT '';
                DROP INDEX IF EXISTS repair_positions;
            ''')
        receipts = self._columns('intake_receipts')
        if receipts and 'boot' not in receipts:
            self.database.executescript('''
                ALTER TABLE intake_receipts RENAME TO intake_receipts_legacy;
                CREATE TABLE intake_receipts (
                    receipt_id INTEGER PRIMARY KEY,
                    device TEXT NOT NULL,
                    boot TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    micrometers INTEGER NOT NULL,
                    raw BLOB NOT NULL,
                    operation TEXT REFERENCES repair_requests (operation),
                    UNIQUE (device, boot, sequence)
                );
                INSERT INTO intake_receipts
                    (receipt_id, device, boot, sequence, slot, micrometers, raw, operation)
                SELECT receipt_id, device, '', sequence, slot, micrometers, raw, operation
                FROM intake_receipts_legacy;
                DROP TABLE intake_receipts_legacy;
            ''')

    def _columns(self, table: str) -> set[str]:
        return {row['name'] for row in self.database.execute(f'PRAGMA table_info({table})')}

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
            'INSERT INTO repair_requests (operation, shift_id, device, boot, sequence) '
            'VALUES (?, ?, ?, ?, ?)',
            (request.operation, request.shift_id, request.device, request.boot, request.sequence),
        )

    def requests_at(self, device: str, boot: str, sequence: int) -> tuple[StoredRepairRequest, ...]:
        rows = self.database.execute('''
            SELECT * FROM repair_requests
            WHERE device = ? AND boot = ? AND sequence = ? ORDER BY operation
        ''', (device, boot, sequence))
        return tuple(StoredRepairRequest(**dict(row)) for row in rows)

    def get_receipt(self, device: str, boot: str, sequence: int) -> StoredReceipt | None:
        row = self.database.execute(
            'SELECT * FROM intake_receipts WHERE device = ? AND boot = ? AND sequence = ?',
            (device, boot, sequence),
        ).fetchone()
        return None if row is None else StoredReceipt(**dict(row))

    def insert_receipt(
        self, device: str, boot: str, sequence: int, slot: int, micrometers: int,
        raw: bytes, operation: str | None,
    ) -> StoredReceipt:
        result = self.database.execute('''
            INSERT INTO intake_receipts (device, boot, sequence, slot, micrometers, raw, operation)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (device, boot, sequence, slot, micrometers, raw, operation))
        return StoredReceipt(
            result.lastrowid, device, boot, sequence, slot, micrometers, raw, operation,
        )

    def list_receipts(
        self, device: str, boot: str, start: int, end: int,
    ) -> tuple[StoredReceipt, ...]:
        rows = self.database.execute('''
            SELECT * FROM intake_receipts
            WHERE device = ? AND boot = ? AND sequence BETWEEN ? AND ?
            ORDER BY sequence
        ''', (device, boot, start, end))
        return tuple(StoredReceipt(**dict(row)) for row in rows)
