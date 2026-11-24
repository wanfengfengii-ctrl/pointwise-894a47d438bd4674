import json
import unittest
from contextlib import ExitStack, closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from station import open_station
from station.capture import receipts_for_range, receive
from station.capture.packets import encode_packet
from station.device import DeviceRepository, ReplaySession, parse_recording, poll
from station.device.slots import read_position
from station.shift import lookup_shift, open_shift


DEVICE = 'V1'
SHIFT = 'morning'


def record(sequence, value, *, device=DEVICE, slot=None):
    if slot is None:
        slot = (sequence - 41) % 2
    return {
        'kind': 'record', 'slot': slot, 'sequence': sequence,
        'payload': encode_packet(device, sequence, slot, value).hex(),
    }


class StationFixture:
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        directory = Path(self.resources.enter_context(TemporaryDirectory()))
        self.station_path = directory / 'station.sqlite'
        self.device_path = directory / 'device.sqlite'
        self.open_database()
        self.open_device()

    def open_database(self):
        if hasattr(self, 'db'):
            self.db.close()
        self.db = self.resources.enter_context(closing(open_station(self.station_path)))

    def open_device(self):
        if hasattr(self, 'device'):
            self.device.close()
        self.device = self.resources.enter_context(closing(DeviceRepository(self.device_path, DEVICE, 2)))

    def session(self, *events):
        return ReplaySession(self.device, parse_recording(json.dumps(events)))

    def register(self, start=41, shift=SHIFT):
        return open_shift(self.db, shift, DEVICE, start)


class CaptureTests(StationFixture, unittest.TestCase):
    def test_normal_receive(self):
        shift = self.register()
        self.assertEqual((shift.shift_id, shift.device, shift.start, shift.end), (SHIFT, DEVICE, 41, None))
        replay = self.session(record(41, 7), record(42, 11), record(43, 9))
        received = tuple(receive(self.db, poll(replay).image) for _ in range(3))
        self.assertIsNone(poll(replay))
        self.assertEqual(receipts_for_range(self.db, DEVICE, 41, 43), received)
        self.assertEqual(
            [(item.packet.sequence, item.packet.micrometers, item.packet.slot) for item in received],
            [(41, 7, 0), (42, 11, 1), (43, 9, 0)],
        )
        self.assertIsNone(read_position(self.device, 41))
        self.assertEqual(receipts_for_range(self.db, 'other', 41, 43), ())

    def test_resume_recording_and_shift(self):
        first_shift = self.register()
        events = [record(41, 7), record(42, 11), record(43, 9)]
        first = receive(self.db, poll(self.session(*events)).image)
        self.open_database()
        self.open_device()
        self.device.initialize()
        self.assertEqual(self.register(), first_shift)
        with self.assertRaises(ValueError):
            open_shift(self.db, SHIFT, 'other', 41)
        self.assertEqual(lookup_shift(self.db, SHIFT), first_shift)
        replay = self.session(*events)
        self.assertEqual(read_position(self.device, 41).raw, first.packet.raw)
        second = receive(self.db, poll(replay).image)
        third = receive(self.db, poll(replay).image)
        self.assertEqual((second.packet.sequence, third.packet.sequence), (42, 43))
        self.assertEqual(poll(replay), None)
        self.assertEqual(receipts_for_range(self.db, DEVICE, 41, 43), (first, second, third))

    def test_invalid_events_preserve_committed_cursor_and_slot(self):
        self.register()
        first = record(41, 7)
        wrong_cover = {'kind': 'cover', 'sequence': 42, 'slot': 0}
        replay = self.session(first, wrong_cover)
        image = poll(replay).image
        with self.assertRaisesRegex(ValueError, 'does not match'):
            poll(replay)
        self.assertEqual(self.device.cursor_state()[0], 1)
        self.assertEqual(read_position(self.device, 41), image)
        with self.assertRaisesRegex(ValueError, 'committed prefix'):
            poll(self.session(record(41, 99), record(42, 11)))
        with self.assertRaises(ValueError):
            parse_recording('[{"kind":"record","slot":false,"sequence":42,"payload":"00"}]')
        self.assertEqual(self.device.cursor_state()[0], 1)
        right_cover = {'kind': 'cover', 'sequence': 41, 'slot': 0}
        self.assertEqual(poll(self.session(first, right_cover)).covered_sequence, 41)
        self.assertIsNone(read_position(self.device, 41))

    def test_bad_packet(self):
        self.register()
        damaged = record(42, 11)
        damaged['payload'] = '00'
        replay = self.session(record(41, 7), damaged, record(43, 9))
        first = receive(self.db, poll(replay).image)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            receive(self.db, poll(replay).image)
        self.assertEqual(receipts_for_range(self.db, DEVICE, 41, 43), (first,))
        last = receive(self.db, poll(replay).image)
        self.assertEqual(receipts_for_range(self.db, DEVICE, 41, 43), (first, last))

    def test_position_mismatch(self):
        self.register()
        replay = self.session(record(41, 7), record(42, 11))
        first = receive(self.db, poll(replay).image)
        image = poll(replay).image
        wrong_body = encode_packet(DEVICE, 43, 1, 11)
        with self.assertRaisesRegex(ValueError, 'delivery origin'):
            receive(self.db, replace(image, raw=wrong_body))
        with self.assertRaisesRegex(ValueError, 'delivery origin'):
            receive(self.db, replace(image, slot=0))
        self.assertEqual(receipts_for_range(self.db, DEVICE, 41, 42), (first,))
        valid = receive(self.db, image)
        self.assertEqual(valid.packet.micrometers, 11)
        self.assertEqual(receive(self.db, image), valid)

    def test_duplicate_receipt(self):
        self.register()
        image = poll(self.session(record(41, 7))).image
        first = receive(self.db, image)
        self.assertEqual(receive(self.db, image), first)
        self.open_database()
        self.assertEqual(receive(self.db, image), first)
        replacement = replace(image, raw=encode_packet(DEVICE, 41, 0, 13))
        with self.assertRaisesRegex(ValueError, 'different original'):
            receive(self.db, replacement)
        self.assertEqual(receipts_for_range(self.db, DEVICE, 41, 41), (first,))
