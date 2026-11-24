"""Stable device registration and fixed shift boundaries in the station database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from station.positions import LEGACY_BOOT


@dataclass(frozen=True)
class ShiftRecord:
    shift_id: str
    device: str
    start: int
    end: int | None = None
    segments: tuple[tuple[str, int, int], ...] | None = None

    def segment_map(self) -> tuple[tuple[str, int, int], ...]:
        """Return the declared (boot, first, last) segments in start order."""
        if self.segments is not None:
            return self.segments
        # An integer-start shift has no fixed end until fix_bounds is called.
        start = self.start
        end = self.end
        if end is None:
            return ()
        return ((LEGACY_BOOT, start, end),)

    def positions(self, boot: str, sequence: int) -> bool:
        if self.segments is None:
            return sequence >= self.start and (self.end is None or sequence <= self.end)
        return any(seg_boot == boot and first <= sequence <= last
                   for seg_boot, first, last in self.segments)

    def ordered_positions(self) -> tuple[tuple[str, int], ...]:
        if self.segments is None:
            if self.end is None:
                raise ValueError('fix the shift bounds before publication')
            return tuple((LEGACY_BOOT, sequence) for sequence in range(self.start, self.end + 1))
        return tuple(
            (boot, sequence)
            for boot, first, last in self.segments
            for sequence in range(first, last + 1)
        )


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
        if row is None:
            return None
        segments = self.get_segments(shift_id) if self.has_segments_table() else ()
        if not segments:
            return ShiftRecord(**dict(row))
        return ShiftRecord(row['shift_id'], row['device'], row['start'], row['end'], segments)

    def has_segments_table(self) -> bool:
        row = self.database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'shift_segments'",
        ).fetchone()
        return row is not None

    def get_segments(self, shift_id: str) -> tuple[tuple[str, int, int], ...] | None:
        rows = self.database.execute('''
            SELECT boot, first_sequence, last_sequence FROM shift_segments
            WHERE shift_id = ? ORDER BY ordinal
        ''', (shift_id,))
        return tuple((row['boot'], row['first_sequence'], row['last_sequence']) for row in rows)

    def insert_shift(self, shift: ShiftRecord) -> None:
        self.database.execute('INSERT OR IGNORE INTO station_devices VALUES (?)', (shift.device,))
        start = shift.start
        end = shift.end
        if shift.segments is not None:
            start = shift.segments[0][1]
            end = shift.segments[-1][2]
        self.database.execute(
            'INSERT INTO shifts (shift_id, device, start, end) VALUES (?, ?, ?, ?)',
            (shift.shift_id, shift.device, start, end),
        )
        if shift.segments is not None:
            self.ensure_segments_table()
            for ordinal, (boot, first, last) in enumerate(shift.segments):
                self.database.execute('''
                    INSERT INTO shift_segments (shift_id, ordinal, boot, first_sequence, last_sequence)
                    VALUES (?, ?, ?, ?, ?)
                ''', (shift.shift_id, ordinal, boot, first, last))

    def ensure_segments_table(self) -> None:
        self.database.execute('''
            CREATE TABLE IF NOT EXISTS shift_segments (
                shift_id TEXT NOT NULL REFERENCES shifts (shift_id),
                ordinal INTEGER NOT NULL,
                boot TEXT NOT NULL,
                first_sequence INTEGER NOT NULL CHECK (first_sequence >= 0),
                last_sequence INTEGER NOT NULL CHECK (last_sequence >= first_sequence),
                PRIMARY KEY (shift_id, ordinal),
                UNIQUE (shift_id, boot)
            )
        ''')

    def set_end(self, shift_id: str, end: int) -> None:
        result = self.database.execute(
            'UPDATE shifts SET end = ? WHERE shift_id = ? AND end IS NULL', (end, shift_id),
        )
        if result.rowcount != 1:
            raise ValueError('shift end has already been fixed')
