"""Test fixtures: a tiny, dependency-free TensorBoard event-file writer.

Emits TFRecord-framed Event/Summary protobuf with valid masked CRC32C and a
leading file_version event, so both the ``--light`` parser and tensorboard's
EventAccumulator accept it — with no tensorflow/torch or committed fixtures.
Covers scalars, text summaries, and histograms.
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


# --- protobuf wire helpers --------------------------------------------------

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


def scalar_value(tag: str, val: float) -> bytes:
    return _ld(1, tag.encode()) + _key(2, 5) + struct.pack("<f", val)


def text_value(tag: str, text: str) -> bytes:
    # TensorProto: dtype=DT_STRING(7) (field 1), string_val (field 8)
    raw = text.encode()
    tensor = _key(1, 0) + _varint(7) + _ld(8, raw)
    return _ld(1, tag.encode()) + _ld(8, tensor)


def histogram_value(tag: str, edges, counts) -> bytes:
    # HistogramProto: bucket_limit (field 6) + bucket counts (field 7), packed
    hp = _key(6, 2) + _varint(len(edges) * 8) + struct.pack(f"<{len(edges)}d", *edges)
    hp += _key(7, 2) + _varint(len(counts) * 8) + struct.pack(f"<{len(counts)}d", *counts)
    return _ld(1, tag.encode()) + _ld(5, hp)


def _event(step: int, wall: float, values) -> bytes:
    e = _key(1, 1) + struct.pack("<d", wall)      # wall_time (double)
    e += _key(2, 0) + _varint(step)               # step (int64)
    return e + _ld(5, b"".join(_ld(1, v) for v in values))  # summary


def _record(payload: bytes) -> bytes:             # TFRecord framing + CRCs
    length = struct.pack("<Q", len(payload))
    return (length + struct.pack("<I", _masked_crc(length))
            + payload + struct.pack("<I", _masked_crc(payload)))


def _file_version() -> bytes:
    return _key(1, 1) + struct.pack("<d", 1000.0) + _ld(3, b"brain.Event:2")


def write_events(path, records) -> None:
    """records: iterable of (step, [value-bytes, ...])."""
    with open(path, "wb") as f:
        f.write(_record(_file_version()))
        for step, values in records:
            f.write(_record(_event(step, 1000.0 + step, values)))


@pytest.fixture
def logdir(tmp_path):
    """One run with two scalar tags (10 steps), a text tag, and a histogram."""
    run = tmp_path / "run_a"
    run.mkdir()
    records = []
    for s in range(0, 100, 10):
        vals = [
            scalar_value("train/loss", math.exp(-s / 50.0)),
            scalar_value("train/acc", s / 100.0),
            histogram_value("weights/h", [0.0, 1.0, 2.0, 3.0],
                            [1.0, 3.0 + s / 10, 2.0, 0.0]),
        ]
        if s == 0:
            vals.append(text_value("note/info", "hello\nworld"))
        records.append((s, vals))
    write_events(run / "events.out.tfevents.1700000000.host.1.0", records)
    return tmp_path
