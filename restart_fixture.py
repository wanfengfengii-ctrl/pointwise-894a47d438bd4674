"""Build the recorder inputs for the two-start shift examples."""

import json

from station.capture.packets import decode_packet, encode_packet


DEVICE = "V1"
SHIFT = "restart-morning"
SEGMENTS = (("A", 41, 42), ("B", 41, 41))
OPERATION = "R42"
SLOT = 1


def record(sequence, micrometers):
    raw = encode_packet(DEVICE, sequence, SLOT, micrometers)
    return {"kind": "record", "slot": SLOT, "sequence": sequence, "payload": raw.hex()}


def recording(second_value=9, *, late=True):
    events = [{"kind": "boot", "boot": "A"}, record(41, 7), record(42, 11)]
    if late:
        events.append({"kind": "issue", "operation": OPERATION})
    events.extend([{"kind": "boot", "boot": "B"}, record(41, second_value)])
    events.append({"kind": "reply" if late else "issue", "operation": OPERATION})
    return json.dumps(events, separators=(",", ":"))


def packet_fields(events):
    packets = (decode_packet(bytes.fromhex(event["payload"]))
               for event in events if event["kind"] == "record")
    return [(packet.device, packet.sequence, packet.slot, packet.micrometers) for packet in packets]


def main():
    late = json.loads(recording())
    equal = json.loads(recording(7))
    gap = json.loads(recording(late=False))
    expected = [(DEVICE, 41, 1, 7), (DEVICE, 42, 1, 11), (DEVICE, 41, 1, 9)]
    if packet_fields(late) != expected or packet_fields(gap) != expected:
        raise AssertionError("fixture changed source bytes or coordinates")
    if equal[1]["payload"] != equal[5]["payload"]:
        raise AssertionError("equal-originals fixture must reuse the exact bytes")
    if [step["kind"] for step in late] != ["boot", "record", "record", "issue", "boot", "record", "reply"]:
        raise AssertionError("late-reply fixture changed recorder order")
    if [step["kind"] for step in gap] != ["boot", "record", "record", "boot", "record", "issue"]:
        raise AssertionError("gap fixture must issue after the slot is overwritten")
    print(json.dumps({"segments": SEGMENTS, "late_readings": packet_fields(late),
                      "equal_raw": True, "gap_events": [event["kind"] for event in gap]}))


if __name__ == "__main__":
    main()
