"""Shared input contract and station database entry point."""

from __future__ import annotations

import sqlite3


MAX_INTEGER = (1 << 63) - 1
MIN_INTEGER = -(1 << 63)


def require_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{field} must be a nonempty name')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise ValueError(f'{field} must be valid UTF-8') from error
    return value


def require_integer(value: object, field: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise ValueError(f'{field} must be an integer in [{minimum}, {MAX_INTEGER}]')
    return value


def open_station(path) -> sqlite3.Connection:
    from station.capture.repository import CaptureRepository
    from station.closeout.repository import StatementRepository
    from station.review.repository import ReviewRepository
    from station.shift.repository import ShiftRepository

    database = sqlite3.connect(path, isolation_level=None)
    try:
        database.row_factory = sqlite3.Row
        database.execute('PRAGMA foreign_keys = ON')
        ShiftRepository(database).initialize()
        CaptureRepository(database).initialize()
        StatementRepository(database).initialize()
        ReviewRepository(database).initialize()
    except BaseException:
        database.close()
        raise
    return database
