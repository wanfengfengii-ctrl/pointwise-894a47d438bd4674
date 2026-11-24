"""The recorder commits independently of the station."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from station import require_integer, require_name
from station.positions import LEGACY_BOOT


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
                prefix TEXT NOT NULL,
                boot TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS boots (
                boot TEXT PRIMARY KEY,
                ordinal INTEGER NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS origins (
                origin_id INTEGER PRIMARY KEY AUTOINCREMENT,
                boot TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                slot INTEGER NOT NULL,
                raw BLOB NOT NULL,
                UNIQUE (boot, sequence)
            );
            CREATE TABLE IF NOT EXISTS slots (
                slot INTEGER PRIMARY KEY,
                boot TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                raw BLOB NOT NULL,
                origin_id INTEGER NOT NULL REFERENCES origins (origin_id),
                UNIQUE (boot, sequence)
            );
            CREATE TABLE IF NOT EXISTS covered (
                boot TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                PRIMARY KEY (boot, sequence)
            );
            CREATE TABLE IF NOT EXISTS operations (
                operation TEXT PRIMARY KEY,
                boot TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending', 'issued', 'not_issued')),
                slot INTEGER,
                raw BLOB,
                origin_id INTEGER,
                CHECK (
                    (state = 'issued' AND slot IS NOT NULL AND raw IS NOT NULL)
                    OR (state <> 'issued' AND slot IS NULL AND raw IS NULL)
                )
            );
        ''')
        self._upgrade_legacy_schema()
        with self.transaction() as database:
            database.execute(
                'INSERT OR IGNORE INTO recorder VALUES (1, ?, ?, 0, ?, ?)',
                (self.device, self.slot_count, bytes(32).hex(), LEGACY_BOOT),
            )
            row = database.execute('SELECT device, slot_count FROM recorder').fetchone()
            if (row['device'], row['slot_count']) != (self.device, self.slot_count):
                raise ValueError('device database identity or slot count does not match')

    def _upgrade_legacy_schema(self) -> None:
        """Accept databases created before boot-aware recording existed."""
        recorder = self._columns('recorder')
        if 'boot' not in recorder:
            self.connection.execute("ALTER TABLE recorder ADD COLUMN boot TEXT NOT NULL DEFAULT ''")
        operations = self._columns('operations')
        if 'boot' not in operations:
            self.connection.execute(
                "ALTER TABLE operations ADD COLUMN boot TEXT NOT NULL DEFAULT ''"
            )
            self.connection.execute('ALTER TABLE operations ADD COLUMN origin_id INTEGER')
        if 'boot' not in self._columns('slots'):
            self._rebuild_slots()
        if 'boot' not in self._columns('covered'):
            self.connection.executescript('''
                ALTER TABLE covered RENAME TO covered_legacy;
                CREATE TABLE covered (
                    boot TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    PRIMARY KEY (boot, sequence)
                );
                INSERT INTO covered (boot, sequence) SELECT '', sequence FROM covered_legacy;
                DROP TABLE covered_legacy;
            ''')

    def _columns(self, table: str) -> set[str]:
        return {row['name'] for row in self.connection.execute(f'PRAGMA table_info({table})')}

    def _rebuild_slots(self) -> None:
        """An older device DB has no boot/origin columns; keep every original."""
        self.connection.executescript('''
            ALTER TABLE slots RENAME TO slots_legacy;
            CREATE TABLE slots (
                slot INTEGER PRIMARY KEY,
                boot TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                raw BLOB NOT NULL,
                origin_id INTEGER NOT NULL REFERENCES origins (origin_id),
                UNIQUE (boot, sequence)
            );
            INSERT INTO origins (boot, sequence, slot, raw)
                SELECT '', sequence, slot, raw FROM slots_legacy ORDER BY sequence;
            INSERT INTO origins (boot, sequence, slot, raw)
                SELECT '', sequence, slot, raw FROM operations
                WHERE state = 'issued'
                  AND NOT EXISTS (
                      SELECT 1 FROM origins AS origin
                      WHERE origin.boot = '' AND origin.sequence = operations.sequence
                  )
                ORDER BY sequence;
            INSERT INTO slots (slot, boot, sequence, raw, origin_id)
                SELECT legacy.slot, '', legacy.sequence, legacy.raw,
                       origin.origin_id
                FROM slots_legacy AS legacy
                JOIN origins AS origin ON origin.boot = '' AND origin.sequence = legacy.sequence;
            DROP TABLE slots_legacy;
        ''')
        # Attach issued legacy repairs to the matching durable origin so their
        # bytes stay retrievable after an in-shift restart overwrites the slot.
        self.connection.execute('''
            UPDATE operations SET origin_id = (
                SELECT origin_id FROM origins
                WHERE origins.boot = '' AND origins.sequence = operations.sequence
            )
            WHERE state = 'issued' AND origin_id IS NULL
        ''')

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

    def current_boot(self) -> str:
        row = self.connection.execute('SELECT boot FROM recorder').fetchone()
        return row['boot']

    def begin_boot(self, boot: str) -> None:
        """Switch the recorder to a newly named start at most once per name."""
        boot = require_name(boot, 'boot')
        if not self.connection.in_transaction:
            raise RuntimeError('a boot switch requires a device transaction')
        if boot == self.current_boot():
            raise ValueError('boot name is already the active start')
        if self.connection.execute('SELECT 1 FROM boots WHERE boot = ?', (boot,)).fetchone():
            raise ValueError('boot name was already used by this recorder')
        ordinal = self.connection.execute(
            'SELECT COALESCE(MAX(ordinal), 0) + 1 FROM boots'
        ).fetchone()[0]
        self.connection.execute('INSERT INTO boots (boot, ordinal) VALUES (?, ?)', (boot, ordinal))
        self.connection.execute('UPDATE recorder SET boot = ?', (boot,))

    def boot_order(self) -> tuple[str, ...]:
        rows = self.connection.execute('SELECT boot FROM boots ORDER BY ordinal')
        return tuple(row['boot'] for row in rows)

    def close(self) -> None:
        self.connection.close()
