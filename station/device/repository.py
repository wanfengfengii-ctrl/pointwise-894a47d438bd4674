"""The recorder commits independently of the station."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from station import require_integer, require_name


class DeviceRepository:
    def __init__(self, path, device: str, slot_count: int):
        self.device = require_name(device, 'device')
        self.slot_count = require_integer(slot_count, 'slot count', 1)
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        try:
            self.initialize()
        except BaseException:
            self.close()
            raise

    def initialize(self) -> None:
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS recorder (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                device TEXT NOT NULL,
                slot_count INTEGER NOT NULL CHECK (slot_count > 0),
                cursor INTEGER NOT NULL CHECK (cursor >= 0),
                prefix TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS slots (
                slot INTEGER PRIMARY KEY,
                sequence INTEGER NOT NULL UNIQUE,
                raw BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS covered (
                sequence INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS operations (
                operation TEXT PRIMARY KEY,
                sequence INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending', 'issued', 'not_issued')),
                slot INTEGER,
                raw BLOB,
                CHECK (
                    (state = 'issued' AND slot IS NOT NULL AND raw IS NOT NULL)
                    OR (state <> 'issued' AND slot IS NULL AND raw IS NULL)
                )
            );
        ''')
        with self.transaction() as database:
            database.execute(
                'INSERT OR IGNORE INTO recorder VALUES (1, ?, ?, 0, ?)',
                (self.device, self.slot_count, bytes(32).hex()),
            )
            row = database.execute('SELECT device, slot_count FROM recorder').fetchone()
            if (row['device'], row['slot_count']) != (self.device, self.slot_count):
                raise ValueError('device database identity or slot count does not match')

    @contextmanager
    def transaction(self, *, write: bool = True):
        self.connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def cursor_state(self) -> tuple[int, str]:
        row = self.connection.execute('SELECT cursor, prefix FROM recorder').fetchone()
        return row['cursor'], row['prefix']

    def advance(self, cursor: int, prefix: str) -> None:
        self.connection.execute('UPDATE recorder SET cursor = ?, prefix = ?', (cursor, prefix))

    def close(self) -> None:
        self.connection.close()
