"""Stable device registration and fixed shift boundaries in the station database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ShiftRecord:
    shift_id: str
    device: str
    start: int
    end: int | None = None


class ShiftRepository:
    def __init__(self, database: sqlite3.Connection):
        self.database = database

    def initialize(self) -> None:
        self.database.executescript('''
            CREATE TABLE IF NOT EXISTS station_devices (
                device TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS shifts (
                shift_id TEXT PRIMARY KEY,
                device TEXT NOT NULL REFERENCES station_devices (device),
                start INTEGER NOT NULL CHECK (start >= 0),
                end INTEGER CHECK (end >= start)
            );
        ''')

    def get_shift(self, shift_id: str) -> ShiftRecord | None:
        row = self.database.execute(
            'SELECT shift_id, device, start, end FROM shifts WHERE shift_id = ?', (shift_id,),
        ).fetchone()
        return None if row is None else ShiftRecord(**dict(row))

    def insert_shift(self, shift: ShiftRecord) -> None:
        self.database.execute('INSERT OR IGNORE INTO station_devices VALUES (?)', (shift.device,))
        self.database.execute(
            'INSERT INTO shifts (shift_id, device, start) VALUES (?, ?, ?)',
            (shift.shift_id, shift.device, shift.start),
        )

    def set_end(self, shift_id: str, end: int) -> None:
        result = self.database.execute(
            'UPDATE shifts SET end = ? WHERE shift_id = ? AND end IS NULL', (end, shift_id),
        )
        if result.rowcount != 1:
            raise ValueError('shift end has already been fixed')
