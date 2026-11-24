"""Deterministic recordings, in-shift restart, handover, and offline review."""

import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import restart_fixture
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


def legacy_scenario(directory):
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
        'checklist': [[row.sequence, row.receipt.packet.micrometers, row.note.reported_um]
                      for row in checklist],
    }
    downstream_expected = {'review': 'mismatch', 'checklist': [[42, 11, 10]]}
    if actual != expected or downstream != downstream_expected:
        raise AssertionError((actual, downstream))
    return {
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


def restart_capture(station_path, device_path):
    """Replay the two-start fixture and return the waiting/signed results."""
    with closing(open_station(station_path)) as database, \
            closing(DeviceRepository(device_path, restart_fixture.DEVICE, 2)) as device:
        open_shift(database, restart_fixture.SHIFT, restart_fixture.DEVICE, restart_fixture.SEGMENTS)
        replay = ReplaySession(device, parse_recording(restart_fixture.recording()))
        poll(replay)  # boot A
        a41 = poll(replay)
        a42 = poll(replay)
        receive(database, a41.image, source=device)  # A42's packet is held, not delivered
        request_repair(
            database, restart_fixture.SHIFT, restart_fixture.DEVICE, ('A', 42),
            restart_fixture.OPERATION, source=device,
        )
        issued = poll(replay)
        if issued.repair.state.value != 'issued' or issued.repair.image != a42.image:
            raise AssertionError('R42 was not issued from the A42 original before the restart')
        poll(replay)  # boot B
        b41 = poll(replay)
        receive(database, b41.image, source=device)
        # A42's issued original is still in flight; only B41 is genuinely
        # outstanding once B41 arrives, so the shift waits on A42 alone.
        waiting = close_shift(database, restart_fixture.SHIFT, source=device)

        device.close()
        reopened = DeviceRepository(device_path, restart_fixture.DEVICE, 2)
        try:
            replay = ReplaySession(reopened, parse_recording(restart_fixture.recording()))
            reply = poll(replay)  # resumes at the still-pending A42 reply
            receive(database, reply.image, operation=restart_fixture.OPERATION, source=reopened)
        finally:
            reopened.close()
    with closing(open_station(station_path)) as database:
        signed = close_shift(database, restart_fixture.SHIFT,
                            source=DeviceRepository(device_path, restart_fixture.DEVICE, 2))
    return waiting, signed.publication


def restart_scenario(directory):
    station_path = Path(directory) / 'restart-station.sqlite'
    device_path = Path(directory) / 'restart-device.sqlite'
    waiting, publication = restart_capture(station_path, device_path)
    statement = publication.statement
    actual = {
        'before_reply': waiting.status,
        'pending': [list(position) for position in waiting.pending],
        'readings_um': [[row.boot, row.sequence, row.receipt.packet.micrometers]
                        for row in statement.positions],
        'origins': [[row.boot, row.sequence, row.receipt.packet.slot, row.receipt.packet.raw.hex()]
                    for row in statement.positions],
        'count': statement.stats.received_count,
        'extrema_um': [statement.stats.minimum_um, statement.stats.maximum_um],
        'missing': [list(position) for position in statement.stats.missing],
        'first_receipt': publication.first_receipt.receipt_id,
    }
    expected = {
        'before_reply': 'waiting',
        'pending': [['A', 42]],
        'readings_um': [['A', 41, 7], ['A', 42, 11], ['B', 41, 9]],
        'origins': None,  # checked structurally below
        'count': 3, 'extrema_um': [7, 11], 'missing': [],
        'first_receipt': 'first/shift/' + restart_fixture.SHIFT,
    }
    if not actual['origins'] or [row[0] for row in actual['origins']] != ['A', 'A', 'B']:
        raise AssertionError(actual['origins'])
    actual_checked = {key: value for key, value in actual.items() if key != 'origins'}
    expected_checked = {key: value for key, value in expected.items() if key != 'origins'}
    if actual_checked != expected_checked:
        raise AssertionError((actual_checked, expected_checked))

    with closing(open_station(station_path)) as database:
        fixed = retrieve(database, publication.statement.result_id)
        if fixed != publication:
            raise AssertionError('offline retrieval changed the restart result')
        matches_a = reconcile(database, fixed.statement.result_id, ('A', 41), 7)
        mismatches_b = reconcile(database, fixed.statement.result_id, ('B', 41), 7)
        checklist = build_checklist(database, fixed.statement.result_id)
    downstream = {
        'a41_paper_7': matches_a.conclusion.value,
        'b41_paper_7': mismatches_b.conclusion.value,
        'checklist': [[row.boot, row.sequence, row.receipt.packet.micrometers, row.note.reported_um]
                      for row in checklist],
    }
    downstream_expected = {
        'a41_paper_7': 'match', 'b41_paper_7': 'mismatch',
        'checklist': [['B', 41, 9, 7]],
    }
    if downstream != downstream_expected:
        raise AssertionError((downstream, downstream_expected))
    return {
        'case_id': 'restart',
        'input': {'segments': [[boot, first, last]
                               for boot, first, last in restart_fixture.SEGMENTS],
                  'recordings': ['A41=7', 'A42=11', 'B41=9'], 'operation': restart_fixture.OPERATION,
                  'operation_position': ['A', 42],
                  'paper': {'A41': 7, 'B41': 7}},
        'expected': expected_checked, 'actual': actual_checked,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream,
        'code_paths': [
            'station/device/repository.py', 'station/device/slots.py', 'station/device/operations.py',
            'station/device/replay.py', 'station/capture/intake.py', 'station/capture/repair.py',
            'station/shift/registry.py', 'station/closeout/coverage.py', 'station/closeout/publish.py',
            'station/review/reconcile.py', 'station/review/checklist.py',
        ],
        'oracle': 'A、B 两启动独立实收同序读数；签出按段序 A41、A42、B41，极值 7/11；'
                  '停源后同号 A41 与 B41 分别核对纸值 7，仅 B41 差异。',
    }


def main():
    with TemporaryDirectory(prefix='vibration-station-') as directory:
        root = Path(directory)
        (root / 'legacy').mkdir()
        (root / 'restart').mkdir()
        legacy = legacy_scenario(str(root / 'legacy'))
        restart = restart_scenario(str(root / 'restart'))
    for scenario in (legacy, restart):
        print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
