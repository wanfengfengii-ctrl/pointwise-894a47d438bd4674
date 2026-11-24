"""Store and retrieve the exact, atomically published result."""

from __future__ import annotations

import sqlite3

from station.closeout.statement import FirstReceipt, Publication, decode_statement, encode_statement


class StatementRepository:
    def __init__(self, database: sqlite3.Connection):
        self.database = database

    def initialize(self) -> None:
        self.database.executescript('''
            CREATE TABLE IF NOT EXISTS statements (
                result_id TEXT PRIMARY KEY,
                shift_id TEXT NOT NULL UNIQUE REFERENCES shifts (shift_id),
                snapshot TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS first_receipts (
                result_id TEXT PRIMARY KEY REFERENCES statements (result_id),
                receipt_id TEXT NOT NULL UNIQUE
            );
        ''')

    def store_publication(self, publication: Publication) -> None:
        if not self.database.in_transaction:
            raise RuntimeError('publication requires a station transaction')
        statement = publication.statement
        first = publication.first_receipt
        if statement.result_id != first.result_id:
            raise ValueError('first receipt is for a different result')
        self.database.execute(
            'INSERT INTO statements (result_id, shift_id, snapshot) VALUES (?, ?, ?)',
            (statement.result_id, statement.shift.shift_id, encode_statement(statement)),
        )
        self.database.execute(
            'INSERT INTO first_receipts (result_id, receipt_id) VALUES (?, ?)',
            (first.result_id, first.receipt_id),
        )

    def _load(self, column: str, identity: str) -> Publication | None:
        # Column comes from the two fixed lookup entry points below.
        row = self.database.execute(f'''
            SELECT s.result_id, s.snapshot, r.receipt_id FROM statements AS s
            JOIN first_receipts AS r ON r.result_id = s.result_id
            WHERE s.{column} = ?
        ''', (identity,)).fetchone()
        if row is None:
            return None
        return Publication(decode_statement(row['snapshot']), FirstReceipt(row['result_id'], row['receipt_id']))

    def get_by_result_id(self, result_id: str) -> Publication | None:
        return self._load('result_id', result_id)

    def get_by_shift(self, shift_id: str) -> Publication | None:
        return self._load('shift_id', shift_id)
