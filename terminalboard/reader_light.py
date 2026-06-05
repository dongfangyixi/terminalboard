"""Self-contained pure-Python TensorBoard event reader (the ``--light`` path).

No tensorboard / tensorflow / protobuf dependency. It decodes just enough of
two formats to extract scalars:

  * TFRecord framing:  <uint64 length><uint32 crc><payload><uint32 crc>
  * protobuf wire format for the Event / Summary / Value / TensorProto messages

Scalars appear in event files in two shapes:
  * legacy: Summary.Value.simple_value (float32, field 2)
  * TF2:    Summary.Value.tensor (a 0-d TensorProto, field 8)
Both are handled. CRCs are not verified (we instead stop cleanly on any
truncated/partial trailing record, which is what live-tailing needs).
"""
from __future__ import annotations

import struct
from typing import Dict, Iterator, List, Optional, Tuple

from .model import Run, ScalarSeries

# --- protobuf wire-format primitives ---------------------------------------


def _read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _iter_fields(buf: bytes) -> Iterator[Tuple[int, int, object]]:
    """Yield (field_number, wire_type, value) for each field in a message.

    value is an int for varints, or raw bytes for fixed64/fixed32/length-delimited.
    """
    pos = 0
    n = len(buf)
    while pos < n:
        key, pos = _read_varint(buf, pos)
        field = key >> 3
        wt = key & 0x7
        if wt == 0:  # varint
            val, pos = _read_varint(buf, pos)
            yield field, wt, val
        elif wt == 1:  # 64-bit
            yield field, wt, buf[pos:pos + 8]
            pos += 8
        elif wt == 2:  # length-delimited
            ln, pos = _read_varint(buf, pos)
            yield field, wt, buf[pos:pos + ln]
            pos += ln
        elif wt == 5:  # 32-bit
            yield field, wt, buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")


# --- message decoders -------------------------------------------------------


def _tensor_to_float(buf: bytes) -> Optional[float]:
    """Extract a single scalar from a TensorProto."""
    dtype: Optional[int] = None
    content: Optional[bytes] = None
    floats: List[float] = []
    doubles: List[float] = []
    ints: List[int] = []
    for f, wt, v in _iter_fields(buf):
        if f == 1 and wt == 0:           # dtype
            dtype = v
        elif f == 4 and wt == 2:         # tensor_content (raw little-endian)
            content = v
        elif f == 5:                     # float_val (packed or repeated)
            if wt == 2:
                floats += list(struct.unpack(f"<{len(v) // 4}f", v))
            elif wt == 5:
                floats.append(struct.unpack("<f", v)[0])
        elif f == 6:                     # double_val
            if wt == 2:
                doubles += list(struct.unpack(f"<{len(v) // 8}d", v))
            elif wt == 1:
                doubles.append(struct.unpack("<d", v)[0])
        elif f == 8:                     # int_val (covers int/bool scalars)
            if wt == 0:
                ints.append(v)
    if content:
        if dtype == 2:       # DT_DOUBLE
            return struct.unpack("<d", content[:8])[0]
        if dtype == 1 or dtype is None:  # DT_FLOAT (default)
            return struct.unpack("<f", content[:4])[0]
        if dtype in (3, 9):  # DT_INT32 / DT_INT64-ish stored raw
            n = 8 if dtype == 9 else 4
            fmt = "<q" if dtype == 9 else "<i"
            return float(struct.unpack(fmt, content[:n])[0])
    if floats:
        return floats[0]
    if doubles:
        return doubles[0]
    if ints:
        return float(ints[0])
    return None


def _parse_value(buf: bytes) -> Optional[Tuple[str, float]]:
    """Decode a Summary.Value -> (tag, scalar) or None if not a scalar."""
    tag: Optional[str] = None
    simple: Optional[float] = None
    tensor_val: Optional[float] = None
    for f, wt, v in _iter_fields(buf):
        if f == 1 and wt == 2:           # tag (string)
            tag = bytes(v).decode("utf-8", "replace")
        elif f == 2 and wt == 5:         # simple_value (float32)
            simple = struct.unpack("<f", v)[0]
        elif f == 8 and wt == 2:         # tensor (TensorProto)
            tensor_val = _tensor_to_float(v)
    if tag is None:
        return None
    val = simple if simple is not None else tensor_val
    if val is None:
        return None
    return tag, val


def _parse_event(buf: bytes) -> Tuple[Optional[int], float, List[Tuple[str, float]]]:
    """Decode an Event -> (step, wall_time, [(tag, value), ...])."""
    step: Optional[int] = None
    wall_time = 0.0
    scalars: List[Tuple[str, float]] = []
    for f, wt, v in _iter_fields(buf):
        if f == 1 and wt == 1:           # wall_time (double)
            wall_time = struct.unpack("<d", v)[0]
        elif f == 2 and wt == 0:         # step (int64)
            step = v
        elif f == 5 and wt == 2:         # summary (Summary message)
            for sf, swt, sv in _iter_fields(v):
                if sf == 1 and swt == 2:  # repeated Value value = 1
                    parsed = _parse_value(sv)
                    if parsed is not None:
                        scalars.append(parsed)
    return step, wall_time, scalars


# --- TFRecord framing -------------------------------------------------------


def _read_records(data: bytes, offset: int) -> Tuple[List[bytes], int]:
    """Return (complete payloads, new_offset). Stops at the first incomplete
    record so partial/in-progress writes are simply retried next poll."""
    payloads: List[bytes] = []
    n = len(data)
    pos = offset
    while pos + 12 <= n:
        (length,) = struct.unpack("<Q", data[pos:pos + 8])
        end = pos + 12 + length + 4
        if end > n:
            break  # record still being written
        payload = data[pos + 12:pos + 12 + length]
        payloads.append(payload)
        pos = end
    return payloads, pos


# --- public reader ----------------------------------------------------------


class LightEventFile:
    """Tails one event file, remembering how far it has read."""

    def __init__(self, path: str):
        self.path = path
        self._offset = 0

    def read_new(self) -> List[Tuple[Optional[int], float, List[Tuple[str, float]]]]:
        try:
            with open(self.path, "rb") as fh:
                data = fh.read()
        except OSError:
            return []
        if len(data) <= self._offset:
            return []
        payloads, new_offset = _read_records(data, self._offset)
        self._offset = new_offset
        events = []
        for p in payloads:
            try:
                events.append(_parse_event(p))
            except (IndexError, struct.error, ValueError):
                continue  # skip a malformed record, keep going
        return events


def collect_run(run: Run, files: List[str], state: Dict[str, LightEventFile]) -> None:
    """Read any new events from this run's files into run.series (in place)."""
    for path in files:
        ef = state.get(path)
        if ef is None:
            ef = LightEventFile(path)
            state[path] = ef
        for step, wall_time, scalars in ef.read_new():
            if step is None:
                continue
            for tag, value in scalars:
                run.get(tag).append(step, value, wall_time)
