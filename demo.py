"""A deterministic recording, shift handover, and offline paper-log review."""

import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from station import open_station
from station.capture import receive, request_repair
from station.capture.packets import encode_packet
from station.closeout import close_shift
from station.device import DeviceRepository, ReplaySession, parse_recording, poll
from station.review import build_checklist, reconcile, retrieve
from station.shift import fix_bounds, open_shift


DEVICE = 'V1'
OPERATION = 'R42'


def sample_event(sequence, slot, value):
    return {
        'kind': 'record', 'sequence': sequence, 'slot': slot,
        'payload': encode_packet(DEVICE, sequence, slot, value).hex(),
    }


def capture(station_path, device_path):
    events = parse_recording(json.dumps([
        sample_event(41, 0, 7), sample_event(42, 1, 11), sample_event(43, 0, 9),
        {'kind': 'issue', 'operation': OPERATION}, sample_event(44, 1, 99),
        {'kind': 'reply', 'operation': OPERATION},
    ]))
    with closing(open_station(station_path)) as database, closing(DeviceRepository(device_path, DEVICE, 2)) as device:
        open_shift(database, 'morning', DEVICE, 41)
        fix_bounds(database, 'morning', 43)
        replay = ReplaySession(device, events)
        receive(database, poll(replay).image)
        original = poll(replay).image
        receive(database, poll(replay).image)
        request_repair(database, 'morning', DEVICE, 42, OPERATION, source=device)
        issued = poll(replay)
        covered = poll(replay)
        waiting = close_shift(database, 'morning', source=device)
        if (issued.repair.state.value, covered.covered_sequence) != ('issued', 42):
            raise AssertionError('recording did not issue R42 before covering position 42')
        if waiting.publication is not None:
            raise AssertionError('the shift published before the issued original arrived')
        try:
            retrieve(database, 'shift/morning')
        except KeyError:
            pass
        else:
            raise AssertionError('an unpublished result is already available')
        reply = poll(replay)
        accepted = receive(database, reply.image, source=device, operation=reply.event.operation)
        if accepted.packet.raw != original.raw:
            raise AssertionError('the station did not retain the original packet')
        signed = close_shift(database, 'morning', source=device)
        return waiting, signed.publication


def main():
    with TemporaryDirectory(prefix='vibration-station-') as directory:
        station_path = Path(directory) / 'station.sqlite'
        waiting, signed = capture(station_path, Path(directory) / 'device.sqlite')
        with closing(open_station(station_path)) as database:
            frozen = retrieve(database, 'shift/morning')
            if frozen != signed:
                raise AssertionError('offline retrieval changed the signed result')
            note = reconcile(database, frozen.statement.result_id, 42, 10)
            checklist = build_checklist(database, frozen.statement.result_id)

        statement = frozen.statement
        stats = statement.stats
        origin = statement.positions[1].receipt.packet
        actual = {
            'before_reply': waiting.status, 'pending': list(waiting.pending),
            'readings_um': [[row.sequence, row.receipt.packet.micrometers] for row in statement.positions],
            'origin_42': [origin.device, origin.sequence, origin.slot],
            'count': stats.received_count, 'extrema_um': [stats.minimum_um, stats.maximum_um],
            'missing': list(stats.missing), 'first_receipt': frozen.first_receipt.receipt_id,
        }
        expected = {
            'before_reply': 'waiting', 'pending': [42], 'readings_um': [[41, 7], [42, 11], [43, 9]],
            'origin_42': ['V1', 42, 1], 'count': 3, 'extrema_um': [7, 11], 'missing': [],
            'first_receipt': 'first/shift/morning',
        }
        downstream = {
            'review': note.conclusion.value,
            'checklist': [[row.sequence, row.receipt.packet.micrometers, row.note.reported_um] for row in checklist],
        }
        downstream_expected = {'review': 'mismatch', 'checklist': [[42, 11, 10]]}
        if actual != expected or downstream != downstream_expected:
            raise AssertionError((actual, downstream))
        scenario = {
            'case_id': 'late',
            'input': {'range': [41, 43], 'values_um': [7, 11, 9], 'operation': 'R42',
                      'order': ['issue', 'cover', 'reply'], 'paper_42_um': 10},
            'expected': expected, 'actual': actual,
            'downstream_expected': downstream_expected, 'downstream_actual': downstream,
            'code_paths': [
                'station/device/replay.py', 'station/device/operations.py', 'station/capture/intake.py',
                'station/closeout/coverage.py', 'station/closeout/statistics.py', 'station/closeout/publish.py',
                'station/review/retrieve.py', 'station/review/reconcile.py', 'station/review/checklist.py',
            ],
            'oracle': '按原序手算三件实测，7 为最小值，11 为最大值；纸面 10 与 42 的原读 11 不符。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
