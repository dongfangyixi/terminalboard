"""Test fixtures: a tiny, dependency-free TensorBoard event-file writer.

It emits the same TFRecord-framed Event/Summary protobuf that real TensorBoard
writes (scalars as ``simple_value``), with valid masked CRC32C checksums and a
leading ``file_version`` event — so both the ``--light`` parser *and*
tensorboard's EventAccumulator (which verifies the CRCs) accept it, with no
tensorflow/torch or committed binary fixtures.
"""
from __future__ import annotations

import math
import struct

import pytest

# --- masked CRC32C (Castagnoli), matching TensorFlow's TFRecord framing ---
_CRC_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (0x82F63B78 & -(_c & 1))
    _CRC_TABLE.append(_c & 0xFFFFFFFF)


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
    return crc ^ 0xFFFFFFFF


def _masked_crc(data: bytes) -> int:
    c = _crc32c(data)
    return (((c >> 15) | (c << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _ld(field: int, data: bytes) -> bytes:        # length-delimited
    return _key(field, 2) + _varint(len(data)) + data


def _value(tag: str, val: float) -> bytes:        # Summary.Value
    return _ld(1, tag.encode()) + _key(2, 5) + struct.pack("<f", val)


def _event(step: int, wall: float, scalars) -> bytes:
    e = _key(1, 1) + struct.pack("<d", wall)      # wall_time (double)
    e += _key(2, 0) + _varint(step)               # step (int64)
    summ = b"".join(_ld(1, _value(t, v)) for t, v in scalars)
    return e + _ld(5, summ)                        # summary (Summary message)


def _record(payload: bytes) -> bytes:             # TFRecord framing + CRCs
    length = struct.pack("<Q", len(payload))
    return (length + struct.pack("<I", _masked_crc(length))
            + payload + struct.pack("<I", _masked_crc(payload)))


def _file_version() -> bytes:                     # Event.file_version = field 3
    return _key(1, 1) + struct.pack("<d", 1000.0) + _ld(3, b"brain.Event:2")


def write_event_file(path, steps, tag_values) -> None:
    """tag_values: {tag: [value aligned with steps]}."""
    with open(path, "wb") as f:
        f.write(_record(_file_version()))
        for i, step in enumerate(steps):
            scalars = [(t, vals[i]) for t, vals in tag_values.items()]
            f.write(_record(_event(step, 1000.0 + step, scalars)))


@pytest.fixture
def logdir(tmp_path):
    """A logdir with one run holding two scalar tags over 10 steps."""
    steps = list(range(0, 100, 10))
    run = tmp_path / "run_a"
    run.mkdir()
    write_event_file(
        run / "events.out.tfevents.1700000000.host.1.0",
        steps,
        {
            "train/loss": [math.exp(-s / 50.0) for s in steps],
            "train/acc": [s / 100.0 for s in steps],
        },
    )
    return tmp_path
