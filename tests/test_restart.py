"""Regression for the one explicit in-shift recorder restart."""

import sqlite3
import unittest
from dataclasses import replace

import restart_fixture as fixture
from station.capture import receipts_for_range, receive, request_repair
from station.closeout import close_shift
from station.device import (
    PositionAvailability, ReplaySession, issue_repair, parse_recording, poll,
    position_availability, query_repair,
)
from station.device.operations import RepairState
from station.review import ReviewConclusion, build_checklist, reconcile, retrieve
from station.review.repository import ReviewRepository
from station.shift import fix_bounds, lookup_shift, open_shift
from tests.test_capture import DEVICE, StationFixture


SHIFT = fixture.SHIFT
SEGMENTS = fixture.SEGMENTS
OPERATION = fixture.OPERATION


class RestartFixture(StationFixture):
    def setUp(self):
        super().setUp()
        self.shift = open_shift(self.db, SHIFT, DEVICE, SEGMENTS)

    def replay(self, *args, **kwargs):
        return ReplaySession(self.device, parse_recording(fixture.recording(*args, **kwargs)))

    @staticmethod
    def next(replay):
        return poll(replay)

    def receive_a41_a42(self, replay):
        self.assertEqual(self.next(replay).boot, 'A')
        a41 = self.next(replay)
        a42 = self.next(replay)
        self.assertEqual(receive(self.db, a41.image, source=self.device).boot, 'A')
        self.assertEqual(receive(self.db, a42.image, source=self.device).boot, 'A')
        return a41, a42

    def go_to_b_start(self, replay):
        """Consume an already-run issue and the boot flip to B; return B41."""
        self.assertEqual(self.next(replay).boot, 'B')
        return self.next(replay)

    def complete_late_run(self):
        replay = self.replay()
        a41, a42 = self.receive_a41_a42(replay)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        issued = self.next(replay)
        self.assertEqual(issued.repair.state, RepairState.ISSUED)
        b41 = self.go_to_b_start(replay)
        receive(self.db, b41.image, source=self.device)
        reply = self.next(replay)
        receive(self.db, reply.image, operation=OPERATION, source=self.device)
        publication = close_shift(self.db, SHIFT, source=self.device).publication
        return publication, a41, a42, b41, reply


class RestartDeviceTests(RestartFixture, unittest.TestCase):
    """Recorder-side boot rules and replay cursor continuity."""

    def test_boot_names_are_distinct_and_never_reused(self):
        from station.device import parse_recording
        from station.capture.packets import encode_packet

        def rec(sequence, value, slot=1):
            return {'kind': 'record', 'slot': slot, 'sequence': sequence,
                    'payload': encode_packet(DEVICE, sequence, slot, value).hex()}

        base = [{'kind': 'boot', 'boot': 'A'}, rec(41, 7), rec(42, 11)]
        replay = self.session(*base)
        for _ in range(3):
            poll(replay)
        for bad in (
            base + [{'kind': 'boot', 'boot': 'A'}],      # same name while active
        ):
            with self.assertRaisesRegex(ValueError, 'already'):
                for _ in range(len(bad)):
                    if poll(self.session(*bad)) is None:
                        break
        good = base + [{'kind': 'boot', 'boot': 'B'}, rec(41, 9)]
        replay = self.session(*good)
        for _ in range(4):
            poll(replay)
        self.assertEqual(self.device.boot_order(), ('A', 'B'))
        reuse_old = good + [{'kind': 'boot', 'boot': 'A'}]
        with self.assertRaisesRegex(ValueError, 'already used'):
            for _ in range(len(reuse_old)):
                if poll(self.session(*reuse_old)) is None:
                    break

    def test_cover_boot_must_match_the_current_slot_start(self):
        from station.capture.packets import encode_packet

        def rec(sequence, value, slot=1):
            return {'kind': 'record', 'slot': slot, 'sequence': sequence,
                    'payload': encode_packet(DEVICE, sequence, slot, value).hex()}

        base = [{'kind': 'boot', 'boot': 'A'}, rec(41, 7), rec(42, 11)]
        replay = self.session(*base)
        for _ in range(3):
            poll(replay)
        wrong = base + [{'kind': 'cover', 'slot': 1, 'sequence': 42, 'boot': 'B'}]
        with self.assertRaisesRegex(ValueError, 'current slot'):
            for _ in range(4):
                poll(self.session(*wrong))
        right = base + [{'kind': 'cover', 'slot': 1, 'sequence': 42, 'boot': 'A'}]
        replay = self.session(*right)
        # The failed cover left the committed prefix at 3; the corrected one
        # resumes from the same cursor.
        self.assertEqual(poll(replay).covered_sequence, 42)
        self.assertIsNone(poll(replay))


    def test_relabelled_repair_original_is_rejected(self):
        replay = self.replay()
        self.next(replay)  # boot A
        a41 = self.next(replay)
        receive(self.db, a41.image, source=self.device)
        a42 = self.next(replay)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        issued = self.next(replay)
        self.assertEqual(issued.repair.state, RepairState.ISSUED)
        forged = replace(issued.repair.image, boot='B')
        # A relabelled original cannot be the registered A42 reply.
        with self.assertRaises(ValueError):
            receive(self.db, forged, operation=OPERATION, source=self.device)
        with self.assertRaisesRegex(ValueError, 'origin'):
            receive(self.db, forged, source=self.device)
        # The genuine issued original still lands at A42.
        self.assertEqual(receive(self.db, issued.repair.image, operation=OPERATION,
                                 source=self.device).boot, 'A')

    def test_repair_outcome_survives_device_reopen_and_slot_overwrite(self):
        replay = self.replay()
        self.next(replay)  # boot A
        a41 = self.next(replay)
        receive(self.db, a41.image, source=self.device)
        a42 = self.next(replay)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        self.next(replay)  # issue
        self.next(replay)  # boot B
        b41 = self.next(replay)
        receive(self.db, b41.image, source=self.device)
        self.open_device()
        issued = query_repair(self.device, OPERATION)
        self.assertEqual((issued.boot, issued.sequence, issued.image.slot,
                          issued.image.raw), ('A', 42, 1, a42.image.raw))
        self.assertNotEqual(issued.image.raw, b41.image.raw)
        # Late-arriving A42 packet is the same original, not a new receipt.
        self.assertEqual(receive(self.db, issued.image, operation=OPERATION,
                                 source=self.device).boot, 'A')


class RestartRegistrationTests(RestartFixture, unittest.TestCase):
    def test_segment_registration_is_idempotent_and_strict(self):
        shift = self.shift
        self.assertEqual(shift.segments, SEGMENTS)
        self.assertEqual((shift.start, shift.end), (41, 41))
        self.assertEqual(lookup_shift(self.db, SHIFT), shift)
        self.assertEqual(open_shift(self.db, SHIFT, DEVICE, SEGMENTS), shift)
        with self.assertRaises(ValueError):
            open_shift(self.db, SHIFT, DEVICE, (("A", 41, 42), ("B", 41, 42)))
        with self.assertRaises(ValueError):
            open_shift(self.db, SHIFT, 'other', SEGMENTS)
        with self.assertRaises(ValueError):
            open_shift(self.db, SHIFT, DEVICE, (("A", 41, 42), ("A", 41, 41)))
        with self.assertRaises(ValueError):
            open_shift(self.db, SHIFT, DEVICE, ())

    def test_integer_start_still_registers_a_legacy_shift(self):
        legacy = open_shift(self.db, 'legacy', DEVICE, 41)
        self.assertIsNone(legacy.segments)
        self.assertEqual(open_shift(self.db, 'legacy', DEVICE, 41), legacy)
        self.assertEqual(fix_bounds(self.db, 'legacy', 41).end, 41)
        self.assertEqual(lookup_shift(self.db, 'legacy'), fix_bounds(self.db, 'legacy', 41))


class RestartCaptureTests(RestartFixture, unittest.TestCase):
    def test_receipt_packet_exposes_start_and_redelivery_reclaims_it(self):
        replay = self.replay()
        self.assertEqual(self.next(replay).boot, 'A')
        a41 = self.next(replay)
        receipt = receive(self.db, a41.image, source=self.device)
        self.assertEqual(
            (receipt.packet.boot, receipt.packet.sequence, receipt.packet.slot,
             receipt.packet.raw), ('A', 41, 1, a41.image.raw),
        )
        self.assertEqual(receipt.packet.micrometers, 7)
        # A legal same-position redelivery reclaims the original receipt.
        self.assertEqual(receive(self.db, a41.image, source=self.device), receipt)
        self.assertEqual(len(receipts_for_range(self.db, DEVICE, 41, 41, 'A')), 1)

    def test_two_starts_keep_original_order_slot_and_bytes(self):
        replay = self.replay()
        a41, a42 = self.receive_a41_a42(replay)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        self.assertEqual(self.next(replay).repair.state, RepairState.ISSUED)
        b41 = self.go_to_b_start(replay)
        received_b = receive(self.db, b41.image, source=self.device)
        self.assertEqual(received_b.packet.micrometers, 9)
        self.assertEqual(b41.covered_sequence, 42)
        self.assertEqual((a41.image.slot, b41.image.slot, a41.image.raw == b41.image.raw),
                         (1, 1, False))
        reply = self.next(replay)
        self.assertIsNotNone(reply)
        self.assertEqual(reply.image.boot, 'A')
        self.assertEqual((reply.image.sequence, reply.image.slot), (42, 1))
        first_a42 = receipts_for_range(self.db, DEVICE, 42, 42, 'A')[0]
        self.assertEqual(receive(self.db, reply.image, operation=OPERATION,
                                 source=self.device).receipt_id, first_a42.receipt_id)

        self.open_device()
        first_a41 = receipts_for_range(self.db, DEVICE, 41, 41, 'A')[0]
        second_b41 = receipts_for_range(self.db, DEVICE, 41, 41, 'B')[0]
        self.assertEqual((first_a41.boot, second_b41.boot), ('A', 'B'))
        self.assertNotEqual(first_a41.receipt_id, second_b41.receipt_id)
        self.assertEqual(first_a41.packet.raw, a41.image.raw)
        self.assertEqual(second_b41.packet.raw, b41.image.raw)
        self.assertEqual(query_repair(self.device, OPERATION).image, reply.image)
        self.assertEqual(query_repair(self.device, OPERATION).boot, 'A')

        publication = close_shift(self.db, SHIFT, source=self.device).publication
        statement = publication.statement
        self.assertEqual(
            [(row.boot, row.sequence, row.state, row.receipt.packet.micrometers)
             for row in statement.positions],
            [('A', 41, 'received', 7), ('A', 42, 'received', 11), ('B', 41, 'received', 9)],
        )
        self.assertEqual(
            (statement.stats.received_count, statement.stats.minimum_um, statement.stats.maximum_um,
             statement.stats.missing), (3, 7, 11, ()),
        )

    def test_issued_packet_is_pending_until_it_actually_arrives(self):
        replay = self.replay()
        self.assertEqual(self.next(replay).boot, 'A')
        a41 = self.next(replay)
        a42 = self.next(replay)
        receive(self.db, a41.image, source=self.device)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        issued = self.next(replay)
        self.assertEqual(issued.repair.state, RepairState.ISSUED)
        self.assertEqual(issued.repair.image.raw, a42.image.raw)
        b41 = self.go_to_b_start(replay)
        self.assertEqual(b41.covered_sequence, 42)
        # A42 has left the slot but its issued original has not reached the
        # station; B41 is still undelivered.  Both await a real packet.
        waiting = close_shift(self.db, SHIFT, source=self.device)
        self.assertEqual(waiting.status, 'waiting')
        self.assertEqual(waiting.pending, (('A', 42), ('B', 41)))

    def test_equal_bytes_do_not_merge_starts_and_mislabel_is_rejected(self):
        replay = self.replay(7)
        self.assertEqual(self.next(replay).boot, 'A')
        a41 = self.next(replay)
        receive(self.db, a41.image, source=self.device)
        self.next(replay)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        self.next(replay)  # issue
        b41 = self.go_to_b_start(replay)
        self.assertEqual((a41.image.slot, a41.image.raw), (b41.image.slot, b41.image.raw))
        forged = replace(a41.image, boot='B')
        with self.assertRaisesRegex(ValueError, 'origin'):
            receive(self.db, forged, source=self.device)
        self.assertEqual(position_availability(self.device, ('B', 41)), PositionAvailability.READABLE)
        received_b = receive(self.db, b41.image, source=self.device)
        self.assertEqual((received_b.boot, received_b.packet.sequence), ('B', 41))
        self.assertEqual(receive(self.db, a41.image, source=self.device).boot, 'A')
        self.assertEqual(receive(self.db, b41.image, source=self.device).boot, 'B')
        self.assertEqual(len(receipts_for_range(self.db, DEVICE, 41, 41, 'A')), 1)
        self.assertEqual(len(receipts_for_range(self.db, DEVICE, 41, 41, 'B')), 1)
        self.assertNotEqual(receipts_for_range(self.db, DEVICE, 41, 41, 'A')[0].receipt_id,
                            receipts_for_range(self.db, DEVICE, 41, 41, 'B')[0].receipt_id)

    def test_boot_delivery_requires_the_recorder_source(self):
        replay = self.replay()
        self.next(replay)
        image = self.next(replay).image
        with self.assertRaisesRegex(ValueError, 'recorder source'):
            receive(self.db, image)

    def test_repair_position_binding_and_independence(self):
        replay = self.replay()
        self.assertEqual(self.next(replay).boot, 'A')
        a41 = self.next(replay)
        receive(self.db, a41.image, source=self.device)
        a42 = self.next(replay)  # A42
        self.assertEqual(a42.image.boot, 'A')
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        self.assertEqual(query_repair(self.device, OPERATION).state, RepairState.PENDING)
        issued = self.next(replay)  # issue event
        self.assertEqual(issued.repair.state, RepairState.ISSUED)
        b41 = self.go_to_b_start(replay)
        self.assertEqual(b41.covered_sequence, 42)
        self.assertEqual(position_availability(self.device, ('A', 42)), PositionAvailability.COVERED)
        self.assertEqual(position_availability(self.device, ('B', 42)), PositionAvailability.UNOBSERVED)
        self.open_device()
        issued = query_repair(self.device, OPERATION)
        self.assertEqual((issued.boot, issued.sequence, issued.image.slot), ('A', 42, 1))
        self.assertEqual(issued.image.raw, a42.image.raw)
        self.assertNotEqual(issued.image.raw, b41.image.raw)
        with self.assertRaises(ValueError):
            request_repair(self.db, SHIFT, DEVICE, ('B', 42), OPERATION, source=self.device)
        with self.assertRaises(ValueError):
            request_repair(self.db, SHIFT, DEVICE, ('A', 43), 'other', source=self.device)
        same = request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        self.assertEqual((same.shift_id, same.boot, same.sequence), (SHIFT, 'A', 42))
        request_repair(self.db, SHIFT, DEVICE, ('B', 41), 'RB41', source=self.device)
        self.assertEqual(issue_repair(self.device, 'RB41').state, RepairState.ISSUED)
        self.assertEqual(issue_repair(self.device, 'RB41').image.boot, 'B')


class RestartCloseoutTests(RestartFixture, unittest.TestCase):
    def test_confirmed_gap_after_slot_overwrite(self):
        replay = self.replay(late=False)
        self.assertEqual(self.next(replay).boot, 'A')
        a41 = self.next(replay)
        receive(self.db, a41.image, source=self.device)
        self.next(replay)  # A42, never delivered
        b41 = self.go_to_b_start(replay)
        receive(self.db, b41.image, source=self.device)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        issued = self.next(replay)
        self.assertEqual(issued.repair.state, RepairState.NOT_ISSUED)
        self.assertEqual(position_availability(self.device, ('A', 42)), PositionAvailability.COVERED)
        publication = close_shift(self.db, SHIFT, source=self.device).publication
        statement = publication.statement
        self.assertEqual(
            [(row.boot, row.sequence, row.state) for row in statement.positions],
            [('A', 41, 'received'), ('A', 42, 'missing'), ('B', 41, 'received')],
        )
        self.assertEqual(
            (statement.stats.received_count, statement.stats.minimum_um, statement.stats.maximum_um,
             statement.stats.missing), (2, 7, 9, (('A', 42),)),
        )

    def test_unissued_repair_keeps_the_old_start_pending(self):
        replay = self.replay(late=False)
        self.next(replay)  # boot A
        a41 = self.next(replay)
        receive(self.db, a41.image, source=self.device)
        self.next(replay)  # A42, never delivered
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        b41 = self.go_to_b_start(replay)
        receive(self.db, b41.image, source=self.device)
        # Not yet issued: the recorder has not confirmed the old slot is gone.
        waiting = close_shift(self.db, SHIFT, source=self.device)
        self.assertEqual(waiting.status, 'waiting')
        self.assertEqual(waiting.pending, (('A', 42),))

    def test_publish_failure_then_resign_with_existing_receipts(self):
        replay = self.replay()
        self.receive_a41_a42(replay)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        self.next(replay)  # issue
        b41 = self.go_to_b_start(replay)
        receive(self.db, b41.image, source=self.device)
        reply = self.next(replay)
        receive(self.db, reply.image, operation=OPERATION, source=self.device)
        formal_b = receipts_for_range(self.db, DEVICE, 41, 41, 'B')[0]
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
            close_shift(self.db, SHIFT, source=self.device)
        self.open_database()
        with self.assertRaises(KeyError):
            retrieve(self.db, 'shift/' + SHIFT)
        for table in ('statements', 'first_receipts', 'deferred_checks'):
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(receipts_for_range(self.db, DEVICE, 41, 41, 'B'), (formal_b,))
        self.db.execute('DROP TRIGGER fail_publication_commit')
        publication = close_shift(self.db, SHIFT, source=self.device).publication
        self.assertEqual(publication.first_receipt.receipt_id, 'first/shift/' + SHIFT)
        self.open_database()
        self.assertEqual(retrieve(self.db, 'shift/' + SHIFT), publication)
        self.assertEqual(close_shift(self.db, SHIFT, source=self.device).publication, publication)


class RestartReviewTests(RestartFixture, unittest.TestCase):
    def test_offline_retrieve_and_position_aware_review(self):
        replay = self.replay()
        self.receive_a41_a42(replay)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        self.next(replay)
        b41 = self.go_to_b_start(replay)
        receive(self.db, b41.image, source=self.device)
        reply = self.next(replay)
        receive(self.db, reply.image, operation=OPERATION, source=self.device)
        publication = close_shift(self.db, SHIFT, source=self.device).publication
        self.device.close()
        self.open_database()
        fixed = retrieve(self.db, 'shift/' + SHIFT)
        self.assertEqual(fixed, publication)
        note_a = reconcile(self.db, 'shift/' + SHIFT, ('A', 41), 7)
        note_b = reconcile(self.db, 'shift/' + SHIFT, ('B', 41), 7)
        self.assertEqual(note_a.conclusion, ReviewConclusion.MATCH)
        self.assertEqual(note_b.conclusion, ReviewConclusion.MISMATCH)
        with self.assertRaises(ValueError):
            reconcile(self.db, 'shift/' + SHIFT, ('B', 42), 9)
        with self.assertRaises(ValueError):
            reconcile(self.db, 'shift/' + SHIFT, 41, 7)
        with self.assertRaises(KeyError):
            reconcile(self.db, 'shift/unknown', ('A', 41), 7)
        notes = ReviewRepository(self.db).notes_for_result('shift/' + SHIFT)
        self.assertEqual([(n.boot, n.sequence, n.conclusion) for n in notes],
                         [('A', 41, ReviewConclusion.MATCH), ('B', 41, ReviewConclusion.MISMATCH)])
        checklist = build_checklist(self.db, 'shift/' + SHIFT)
        self.assertEqual([(row.boot, row.sequence, row.conclusion) for row in checklist],
                         [('B', 41, ReviewConclusion.MISMATCH)])
        self.assertEqual(checklist[0].receipt.packet.micrometers, 9)

    def test_missing_position_is_itemized_by_start(self):
        replay = self.replay(late=False)
        self.next(replay)  # boot A
        a41 = self.next(replay)
        receive(self.db, a41.image, source=self.device)
        self.next(replay)  # A42, never delivered
        b41 = self.go_to_b_start(replay)
        receive(self.db, b41.image, source=self.device)
        request_repair(self.db, SHIFT, DEVICE, ('A', 42), OPERATION, source=self.device)
        self.assertEqual(self.next(replay).repair.state, RepairState.NOT_ISSUED)
        publication = close_shift(self.db, SHIFT, source=self.device).publication
        self.device.close()
        self.open_database()
        result_id = 'shift/' + SHIFT
        gap = reconcile(self.db, result_id, ('A', 42), 11)
        self.assertEqual(gap.conclusion, ReviewConclusion.MISSING)
        checklist = build_checklist(self.db, result_id)
        self.assertEqual([(row.boot, row.sequence, row.conclusion) for row in checklist],
                         [('A', 42, ReviewConclusion.MISSING)])
        self.assertIsNone(checklist[0].receipt)


class LegacyRestartUnchangedTests(StationFixture, unittest.TestCase):
    """The integer-start entry point and publications keep their old shape."""

    def test_legacy_statement_and_review_positions(self):
        from station.capture import receive
        from station.closeout import close_shift
        from station.review import build_checklist, reconcile
        from station.shift import fix_bounds
        from tests.test_capture import SHIFT as LEGACY_SHIFT, record

        self.register()
        fix_bounds(self.db, LEGACY_SHIFT, 42)
        replay = self.session(record(41, 7), record(42, 11))
        receive(self.db, poll(replay).image)
        receive(self.db, poll(replay).image)
        publication = close_shift(self.db, LEGACY_SHIFT).publication
        self.assertIsNone(publication.statement.shift.segments)
        self.assertEqual([row.sequence for row in publication.statement.positions], [41, 42])
        self.assertEqual(reconcile(self.db, 'shift/' + LEGACY_SHIFT, 41, 7).conclusion,
                         ReviewConclusion.MATCH)
        self.assertEqual(build_checklist(self.db, 'shift/' + LEGACY_SHIFT), ())
