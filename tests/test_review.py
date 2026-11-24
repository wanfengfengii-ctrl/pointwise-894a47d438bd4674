import unittest

from station.capture import receive
from station.closeout import close_shift
from station.device import poll
from station.review import ReviewConclusion, build_checklist, reconcile, retrieve
from station.review.repository import ReviewRepository
from station.shift import fix_bounds
from tests.test_capture import DEVICE, SHIFT, StationFixture, record


class ReviewTests(StationFixture, unittest.TestCase):
    def test_fixed_offline_review(self):
        self.register()
        fix_bounds(self.db, SHIFT, 43)
        replay = self.session(*(record(sequence, value) for sequence, value in (
            (41, 7), (42, 11), (43, 9), (44, 19), (45, 23), (46, 29),
        )))
        for _ in range(3):
            receive(self.db, poll(replay).image)
        first = close_shift(self.db, SHIFT, source=self.device).publication
        self.register(44, 'afternoon')
        fix_bounds(self.db, 'afternoon', 46)
        for _ in range(3):
            receive(self.db, poll(replay).image)
        later = close_shift(self.db, 'afternoon', source=self.device).publication
        self.assertNotEqual(later.first_receipt, first.first_receipt)
        self.device.close()
        self.open_database()
        fixed = retrieve(self.db, 'shift/morning')
        self.assertEqual(fixed, first)
        self.assertEqual(retrieve(self.db, 'shift/afternoon'), later)
        before = fixed.statement.positions[1].receipt.packet
        self.assertEqual((before.device, before.sequence, before.slot, before.micrometers), (DEVICE, 42, 1, 11))
        note = reconcile(self.db, 'shift/morning', 42, 11)
        self.assertEqual(note.conclusion, ReviewConclusion.MATCH)
        self.assertEqual((note.result_id, note.sequence), ('shift/morning', 42))
        with self.assertRaises(ValueError):
            reconcile(self.db, 'shift/morning', 44, 19)
        with self.assertRaises(KeyError):
            reconcile(self.db, 'shift/unknown', 42, 11)
        self.assertEqual(ReviewRepository(self.db).notes_for_result('shift/morning'), (note,))
        self.assertEqual(retrieve(self.db, 'shift/morning'), fixed)

    def test_zero_and_gap_review_persistence(self):
        self.register()
        fix_bounds(self.db, SHIFT, 43)
        replay = self.session(record(41, 0), record(42, 11), record(43, 9), record(44, 20))
        receive(self.db, poll(replay).image)
        poll(replay)
        receive(self.db, poll(replay).image)
        self.assertEqual(poll(replay).covered_sequence, 42)
        frozen = close_shift(self.db, SHIFT, source=self.device).publication
        self.assertEqual(frozen.statement.stats.minimum_um, 0)
        result_id = frozen.statement.result_id
        self.device.close()
        self.open_database()
        # Review order intentionally differs from the order used in the checklist.
        mismatch = reconcile(self.db, result_id, 43, 8)
        gap = reconcile(self.db, result_id, 42, 9)
        match = reconcile(self.db, result_id, 41, 0)
        self.assertEqual((mismatch.conclusion, gap.conclusion, match.conclusion),
                         (ReviewConclusion.MISMATCH, ReviewConclusion.MISSING, ReviewConclusion.MATCH))
        self.open_database()
        self.assertEqual(ReviewRepository(self.db).notes_for_result(result_id), (mismatch, gap, match))
        checklist = build_checklist(self.db, result_id)
        self.assertEqual([(row.sequence, row.conclusion) for row in checklist],
                         [(42, ReviewConclusion.MISSING), (43, ReviewConclusion.MISMATCH)])
        self.assertIsNone(checklist[0].receipt)
        self.assertEqual(checklist[1].note, mismatch)
        self.assertEqual(checklist[1].receipt.packet.micrometers, 9)
        self.assertEqual(retrieve(self.db, result_id), frozen)
