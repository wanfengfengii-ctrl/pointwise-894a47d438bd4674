import sqlite3
import unittest
from dataclasses import replace

from station.capture import receipts_for_range, receive, request_repair
from station.capture.packets import encode_packet
from station.closeout import close_shift
from station.device import issue_repair, poll, query_repair
from station.device.operations import RepairState
from station.device.slots import PositionAvailability, position_availability, read_position
from station.review import retrieve
from station.shift import fix_bounds, lookup_shift
from tests.test_capture import DEVICE, SHIFT, StationFixture, record


class CloseoutTests(StationFixture, unittest.TestCase):
    def opening(self):
        self.register()
        return fix_bounds(self.db, SHIFT, 43)

    def request(self, name='R42', *, dispatch=True):
        return request_repair(
            self.db, SHIFT, DEVICE, 42, name, source=self.device if dispatch else None,
        )

    def assert_waiting(self, *sequences):
        result = close_shift(self.db, SHIFT, source=self.device)
        self.assertEqual(result.status, 'waiting')
        self.assertEqual(result.pending, sequences)
        self.assertIsNone(result.publication)
        with self.assertRaises(KeyError):
            retrieve(self.db, 'shift/morning')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM first_receipts').fetchone()[0], 0)

    def assert_full_readings(self, publication):
        self.assertEqual(publication.first_receipt.receipt_id, 'first/shift/morning')
        statement = publication.statement
        self.assertEqual(
            [(row.sequence, row.state, row.receipt.packet.micrometers) for row in statement.positions],
            [(41, 'received', 7), (42, 'received', 11), (43, 'received', 9)],
        )
        middle = statement.positions[1].receipt
        self.assertEqual((middle.packet.device, middle.packet.sequence, middle.packet.slot), (DEVICE, 42, 1))
        self.assertEqual(middle.operation, 'R42')
        self.assertEqual(
            (statement.stats.received_count, statement.stats.minimum_um, statement.stats.maximum_um,
             statement.stats.missing), (3, 7, 11, ()),
        )

    def test_reply_after_cover(self):
        self.opening()
        events = [record(41, 7), record(42, 11), record(43, 9),
                  {'kind': 'issue', 'operation': 'R42'}, record(44, 99),
                  {'kind': 'reply', 'operation': 'R42'}]
        replay = self.session(*events)
        receive(self.db, poll(replay).image)
        original = poll(replay).image
        receive(self.db, poll(replay).image)
        request = self.request()
        self.assertEqual(query_repair(self.device, 'R42').state, RepairState.PENDING)
        issued = poll(replay)
        self.assertEqual(issued.repair.state, RepairState.ISSUED)
        self.assertIsNone(issued.image)
        self.assertEqual(poll(replay).covered_sequence, 42)
        self.assertIsNone(read_position(self.device, 42))
        self.assert_waiting(42)

        self.open_device()
        self.assertEqual(query_repair(self.device, 'R42'), issued.repair)
        self.assertEqual(issue_repair(self.device, 'R42'), issued.repair)
        self.assertEqual(self.request(), request)
        with self.assertRaises(ValueError):
            request_repair(self.db, SHIFT, DEVICE, 43, 'R42', source=self.device)
        response = poll(self.session(*events))
        self.assertEqual(response.image, original)
        self.assertEqual(response.repair.operation, 'R42')
        forged = replace(original, raw=encode_packet(DEVICE, 42, 1, 13))
        with self.assertRaisesRegex(ValueError, 'issued original'):
            receive(self.db, forged, operation='R42', source=self.device)
        with self.assertRaisesRegex(ValueError, 'registered request'):
            receive(self.db, original, operation='unregistered', source=self.device)
        receipt = receive(self.db, response.image, operation='R42', source=self.device)
        self.assertEqual(receipt.packet.raw, original.raw)
        publication = close_shift(self.db, SHIFT, source=self.device).publication
        self.assert_full_readings(publication)
        self.assertEqual(retrieve(self.db, 'shift/morning'), publication)

    def test_reopen_before_durable_intake(self):
        self.opening()
        events = [record(41, 7), record(42, 11), record(43, 9), record(44, 99)]
        replay = self.session(*events)
        receive(self.db, poll(replay).image)
        poll(replay)
        self.request()
        reply = issue_repair(self.device, 'R42').image
        self.open_database()
        self.open_device()
        replay = self.session(*events)
        receive(self.db, poll(replay).image)
        self.assertEqual(poll(replay).covered_sequence, 42)
        self.assert_waiting(42)
        first = receive(self.db, reply, operation='R42', source=self.device)
        self.open_database()
        self.assertEqual(receive(self.db, reply, operation='R42', source=self.device), first)
        self.assertEqual(len(receipts_for_range(self.db, DEVICE, 42, 42)), 1)
        self.assert_full_readings(close_shift(self.db, SHIFT, source=self.device).publication)

    def test_delayed_request(self):
        self.opening()
        replay = self.session(record(41, 7), record(42, 11), record(43, 9), record(44, 99))
        receive(self.db, poll(replay).image)
        original = poll(replay).image
        receive(self.db, poll(replay).image)
        self.request()
        issue_repair(self.device, 'R42')
        delayed = self.request('late-dispatch', dispatch=False)
        self.assertEqual(poll(replay).covered_sequence, 42)
        self.assertEqual(query_repair(self.device, 'late-dispatch').state, RepairState.UNKNOWN)
        self.assert_waiting(42)
        self.assertEqual(self.request('late-dispatch'), delayed)
        self.assertEqual(query_repair(self.device, 'late-dispatch').state, RepairState.PENDING)
        self.assertEqual(issue_repair(self.device, 'late-dispatch').state, RepairState.NOT_ISSUED)
        self.assert_waiting(42)
        receive(self.db, original, operation='R42', source=self.device)
        self.assert_full_readings(close_shift(self.db, SHIFT, source=self.device).publication)

    def test_confirmed_gap(self):
        bounds = self.opening()
        replay = self.session(record(41, 7), record(42, 11), record(43, 9), record(44, 99))
        receive(self.db, poll(replay).image)
        poll(replay)
        receive(self.db, poll(replay).image)
        self.request()
        out_of_shift = poll(replay)
        self.assertEqual(out_of_shift.covered_sequence, 42)
        receive(self.db, out_of_shift.image)
        self.assertEqual(fix_bounds(self.db, SHIFT, 43), bounds)
        with self.assertRaises(ValueError):
            fix_bounds(self.db, SHIFT, 44)
        with self.assertRaises(ValueError):
            fix_bounds(self.db, SHIFT, 40)
        self.assertEqual(position_availability(self.device, 42), PositionAvailability.COVERED)
        self.assert_waiting(42)
        self.assertEqual(issue_repair(self.device, 'R42').state, RepairState.NOT_ISSUED)
        self.open_database()
        self.open_device()
        self.assertEqual(lookup_shift(self.db, SHIFT), bounds)
        self.assertEqual(query_repair(self.device, 'R42').state, RepairState.NOT_ISSUED)
        statement = close_shift(self.db, SHIFT, source=self.device).publication.statement
        self.assertEqual([(row.sequence, row.state) for row in statement.positions],
                         [(41, 'received'), (42, 'missing'), (43, 'received')])
        self.assertEqual(
            (statement.stats.received_count, statement.stats.minimum_um, statement.stats.maximum_um,
             statement.stats.missing), (2, 7, 9, (42,)),
        )
        self.assertEqual(receipts_for_range(self.db, DEVICE, 44, 44)[0].packet.micrometers, 99)

    def test_unknown_operation_is_not_negative_evidence(self):
        self.opening()
        self.request(dispatch=False)
        replay = self.session(record(41, 7), record(42, 11), record(43, 9), record(44, 99))
        receive(self.db, poll(replay).image)
        poll(replay)
        receive(self.db, poll(replay).image)
        poll(replay)
        self.assertEqual(query_repair(self.device, 'R42').state, RepairState.UNKNOWN)
        self.assert_waiting(42)
        self.request()
        self.assertEqual(issue_repair(self.device, 'R42').state, RepairState.NOT_ISSUED)
        self.assertEqual(close_shift(self.db, SHIFT, source=self.device).publication.statement.stats.missing, (42,))

    def test_all_missing(self):
        self.opening()
        replay = self.session(*(record(sequence, sequence) for sequence in range(41, 46)))
        self.assert_waiting(41, 42, 43)
        for _ in range(5):
            poll(replay)
        statement = close_shift(self.db, SHIFT, source=self.device).publication.statement
        self.assertEqual([(row.sequence, row.state, row.receipt) for row in statement.positions],
                         [(41, 'missing', None), (42, 'missing', None), (43, 'missing', None)])
        self.assertEqual(
            (statement.stats.received_count, statement.stats.minimum_um, statement.stats.maximum_um,
             statement.stats.missing), (0, None, None, (41, 42, 43)),
        )

    def test_failed_publish(self):
        self.opening()
        replay = self.session(record(41, 7), record(42, 11), record(43, 9))
        formal = tuple(receive(self.db, poll(replay).image) for _ in range(3))
        self.db.executescript('''
            CREATE TABLE approvals (id INTEGER PRIMARY KEY);
            CREATE TABLE deferred_checks (
                id INTEGER REFERENCES approvals (id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_publication_commit AFTER INSERT ON first_receipts
            BEGIN
                INSERT INTO deferred_checks VALUES (1);
            END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            close_shift(self.db, SHIFT)
        self.open_database()
        with self.assertRaises(KeyError):
            retrieve(self.db, 'shift/morning')
        for table in ('statements', 'first_receipts', 'deferred_checks'):
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(receipts_for_range(self.db, DEVICE, 41, 43), formal)
        self.db.execute('DROP TRIGGER fail_publication_commit')
        publication = close_shift(self.db, SHIFT).publication
        self.assertEqual(tuple(row.receipt for row in publication.statement.positions), formal)
        self.open_database()
        self.assertEqual(retrieve(self.db, 'shift/morning'), publication)
        self.assertEqual(close_shift(self.db, SHIFT).publication, publication)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM first_receipts').fetchone()[0], 1)
