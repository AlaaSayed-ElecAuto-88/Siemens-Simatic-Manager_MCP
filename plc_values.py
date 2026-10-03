"""STEP 7 operand addresses and elementary data types for live PLC values."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Any

from s7client import AREA_COUNTER, AREA_DB, AREA_FLAG, AREA_INPUT, AREA_OUTPUT, AREA_TIMER, Item

# English and German mnemonics; peripheral operands are read from the process image.
MEMORY_AREAS = {"I": AREA_INPUT, "E": AREA_INPUT, "PI": AREA_INPUT, "PE": AREA_INPUT,
                "Q": AREA_OUTPUT, "A": AREA_OUTPUT, "PQ": AREA_OUTPUT, "PA": AREA_OUTPUT, "M": AREA_FLAG}
WIDTHS = {"B": 1, "W": 2, "D": 4}
TYPE_SIZES = {"BOOL": 1, "BYTE": 1, "CHAR": 1, "WORD": 2, "INT": 2, "S5TIME": 2, "DATE": 2,
              "DWORD": 4, "DINT": 4, "REAL": 4, "TIME": 4, "TIME_OF_DAY": 4, "TIMER": 2, "COUNTER": 2}
DEFAULT_TYPES = {1: "BYTE", 2: "WORD", 4: "DWORD"}

_MEMORY = re.compile(r"^(PI|PE|PQ|PA|I|E|Q|A|M)\s*([BWD]?)\s*(\d+)(?:\.([0-7]))?$")
_DB = re.compile(r"^DB\s*(\d+)\s*\.\s*DB([XBWD])\s*(\d+)(?:\.([0-7]))?$")
_TIMER_COUNTER = re.compile(r"^(T|C|Z)\s*(\d+)$")


@dataclass(frozen=True)
class Operand:
    item: Item
    data_type: str
    address: str


def parse_address(text: str, data_type: str | None = None) -> Operand:
    """Parse "I 0.0", "MW 10", "DB10.DBD4", "T 5", "C 3"; an optional ":TYPE" suffix or data_type overrides the type."""
    text = text.strip()
    if ":" in text:
        text, data_type = (part.strip() for part in text.rsplit(":", 1))
    upper = text.upper()
    data_type = data_type.upper().replace(" ", "_") if data_type else None

    if m := _TIMER_COUNTER.match(upper):
        is_timer = m[1] == "T"
        item = Item(AREA_TIMER if is_timer else AREA_COUNTER, int(m[2]), size=2)
        return Operand(item, "TIMER" if is_timer else "COUNTER", f"{m[1]} {m[2]}")

    if m := _DB.match(upper):
        db, width, start, bit = int(m[1]), m[2], int(m[3]), m[4]
        area, label = AREA_DB, f"DB{db}.DB{width}{start}"
    elif m := _MEMORY.match(upper):
        db, width, start, bit = 0, m[2] or "X", int(m[3]), m[4]
        area, label = MEMORY_AREAS[m[1]], f"{m[1]}{m[2]} {start}"
    else:
        raise ValueError(f"'{text}' is not a valid operand address (examples: I 0.0, MW 10, DB10.DBD4, T 5).")

    if width == "X":
        if bit is None:
            raise ValueError(f"'{text}' needs a bit number, e.g. {text}.0")
        return Operand(Item(area, start, db=db, bit=int(bit)), "BOOL", f"{label}.{bit}")
    if bit is not None:
        raise ValueError(f"'{text}': a byte, word or double word address cannot have a bit number.")
    size = WIDTHS[width]
    data_type = data_type or DEFAULT_TYPES[size]
    if TYPE_SIZES.get(data_type) != size or data_type in ("BOOL", "TIMER", "COUNTER"):
        raise ValueError(f"Data type {data_type} does not fit operand '{text}'.")
    return Operand(Item(area, start, size=size, db=db), data_type, label)


def _bcd(value: int) -> int:
    return int(f"{value:x}") if all(c in "0123456789" for c in f"{value:x}") else 0


def _s5time_seconds(raw: int) -> float:
    return _bcd(raw & 0x0FFF) * 0.01 * 10 ** ((raw >> 12) & 3)


def decode(operand: Operand, raw: bytes) -> Any:
    t = operand.data_type
    if t == "BOOL":
        return bool(raw[0] >> operand.item.bit & 1)
    if t in ("BYTE", "WORD", "DWORD"):
        return int.from_bytes(raw, "big")
    if t in ("INT", "DINT"):
        return int.from_bytes(raw, "big", signed=True)
    if t == "REAL":
        return round(struct.unpack(">f", raw)[0], 6)
    if t == "CHAR":
        return raw.decode("latin-1")
    if t in ("S5TIME", "TIMER"):
        return {"seconds": round(_s5time_seconds(int.from_bytes(raw, "big")), 2)}
    if t == "COUNTER":
        return _bcd(int.from_bytes(raw, "big") & 0x0FFF)
    if t == "TIME":
        return {"milliseconds": int.from_bytes(raw, "big", signed=True)}
    if t == "TIME_OF_DAY":
        ms = int.from_bytes(raw, "big")
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"
    if t == "DATE":
        return {"days_since_1990_01_01": int.from_bytes(raw, "big")}
    raise ValueError(f"Data type {t} cannot be shown as a simple value.")


def _to_int(value: Any) -> int:
    if isinstance(value, str):
        text = value.strip().upper().replace("_", "")
        for prefix in ("DW#16#", "W#16#", "B#16#", "16#"):
            if text.startswith(prefix):
                return int(text[len(prefix):], 16)
        if text.startswith("2#"):
            return int(text[2:], 2)
        return int(text, 0)
    return int(value)


def encode(operand: Operand, value: Any) -> bytes:
    t, size = operand.data_type, operand.item.size
    if t == "BOOL":
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "on")
        return bytes([1 if value else 0])
    if t in ("BYTE", "WORD", "DWORD"):
        return _to_int(value).to_bytes(size, "big")
    if t in ("INT", "DINT"):
        return _to_int(value).to_bytes(size, "big", signed=True)
    if t == "REAL":
        return struct.pack(">f", float(value))
    if t == "CHAR":
        return str(value).encode("latin-1")[:1].ljust(1, b" ")
    if t == "TIME":
        return _to_int(value).to_bytes(4, "big", signed=True)
    if t == "S5TIME":
        ticks = round(float(value) * 100)          # value in seconds, 10 ms ticks
        base = 0
        while ticks > 999 and base < 3:
            ticks, base = round(ticks / 10), base + 1
        if not 0 <= ticks <= 999:
            raise ValueError("S5TIME must be between 0 and 9990 seconds.")
        return (base << 12 | int(str(ticks), 16)).to_bytes(2, "big")
    raise ValueError(f"Writing data type {t} is not supported.")
