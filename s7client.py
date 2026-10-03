"""Minimal S7 protocol client (S7-300/400): read/write memory and read system status lists.

Two transports carry the S7 messages:
- S7OnlineTransport: through STEP 7's PG/PC interface (reaches S7-PLCSIM when it is running)
- TcpTransport: straight to a CPU or CP over ISO-on-TCP, no STEP 7 involved
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

from worker import Bridge

AREA_INPUT, AREA_OUTPUT, AREA_FLAG, AREA_DB = 0x81, 0x82, 0x83, 0x84
AREA_COUNTER, AREA_TIMER = 0x1C, 0x1D

ITEM_ERRORS = {
    0x01: "hardware fault",
    0x03: "access denied",
    0x05: "address out of range",
    0x06: "data type not supported",
    0x07: "data type inconsistent",
    0x0A: "object does not exist (block not loaded)",
}


class S7Error(RuntimeError):
    pass


@dataclass(frozen=True)
class Item:
    """One operand: `size` bytes at byte `start`, a single bit when `bit` is set, or timer/counter number `start`."""

    area: int
    start: int
    size: int = 1
    db: int = 0
    bit: int | None = None

    @property
    def is_timer_or_counter(self) -> bool:
        return self.area in (AREA_COUNTER, AREA_TIMER)


class S7OnlineTransport:
    def __init__(self, address: str, rack: int, slot: int) -> None:
        self._link = Bridge("s7online_link.ps1", label="S7ONLINE")
        self._link.call("open", timeout=30, address=address, rack=rack, slot=slot)

    def exchange(self, pdu: bytes) -> bytes:
        return bytes.fromhex(self._link.call("exchange", timeout=30, pdu=pdu.hex())["pdu"])

    def close(self) -> None:
        self._link.stop()


class TcpTransport:
    def __init__(self, host: str, rack: int, slot: int, port: int = 102, timeout: float = 5.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        # COTP connection request: TPDU size 1024, source TSAP 01.00 (PG), destination TSAP 01.<rack/slot>
        request = bytes([0x11, 0xE0, 0, 0, 0, 1, 0, 0xC0, 1, 0x0A, 0xC1, 2, 0x01, 0x00, 0xC2, 2, 0x01, rack << 5 | slot])
        self._send(request)
        if self._receive()[1] != 0xD0:
            raise S7Error(f"{host} refused the ISO connection (check rack/slot).")

    def _send(self, payload: bytes) -> None:
        self._sock.sendall(b"\x03\x00" + (len(payload) + 4).to_bytes(2, "big") + payload)

    def _receive_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise S7Error("The PLC closed the connection.")
            data += chunk
        return data

    def _receive(self) -> bytes:
        header = self._receive_exact(4)
        return self._receive_exact(int.from_bytes(header[2:4], "big") - 4)

    def exchange(self, pdu: bytes) -> bytes:
        self._send(b"\x02\xf0\x80" + pdu)
        reply = b""
        while True:
            frame = self._receive()
            reply += frame[frame[0] + 1:]
            if frame[2] & 0x80:
                return reply

    def close(self) -> None:
        self._sock.close()


class S7Client:
    def __init__(self, transport: S7OnlineTransport | TcpTransport) -> None:
        self._transport = transport
        self._ref = 0
        self.pdu_size = 240
        param, _ = self._job(bytes([0xF0, 0]) + struct.pack(">HHH", 1, 1, 480))
        self.pdu_size = struct.unpack(">H", param[6:8])[0]

    def close(self) -> None:
        self._transport.close()

    def _next_ref(self) -> int:
        self._ref = self._ref % 0xFFFF + 1
        return self._ref

    def _job(self, param: bytes, data: bytes = b"") -> tuple[bytes, bytes]:
        header = struct.pack(">BBHHHH", 0x32, 1, 0, self._next_ref(), len(param), len(data))
        reply = self._transport.exchange(header + param + data)
        if len(reply) < 12 or reply[0] != 0x32 or reply[1] not in (2, 3):
            raise S7Error("Unexpected answer from the PLC.")
        param_len, _, err_class, err_code = struct.unpack(">HHBB", reply[6:12])
        if err_class or err_code:
            raise S7Error(f"The PLC rejected the request (error class 0x{err_class:02X}, code 0x{err_code:02X}).")
        return reply[12:12 + param_len], reply[12 + param_len:]

    @staticmethod
    def _item_spec(item: Item, as_bit: bool) -> bytes:
        if item.is_timer_or_counter:
            transport, length, address = item.area, 1, item.start
        elif as_bit:
            transport, length, address = 1, 1, item.start * 8 + (item.bit or 0)
        else:
            transport, length, address = 2, item.size, item.start * 8
        return bytes([0x12, 0x0A, 0x10, transport]) + struct.pack(">HHB", length, item.db, item.area) + address.to_bytes(3, "big")

    def read(self, items: list[Item]) -> list[bytes | S7Error]:
        """Read operands; each result is the raw bytes (bits come back as their whole byte) or an S7Error."""
        results: list[bytes | S7Error] = []
        batch: list[Item] = []
        reply_size = 0
        for item in items:
            size = 4 + (2 if item.is_timer_or_counter else item.size) + 1
            if batch and (len(batch) >= 18 or reply_size + size > self.pdu_size - 20):
                results += self._read_batch(batch)
                batch, reply_size = [], 0
            batch.append(item)
            reply_size += size
        if batch:
            results += self._read_batch(batch)
        return results

    def _read_batch(self, items: list[Item]) -> list[bytes | S7Error]:
        _, data = self._job(bytes([0x04, len(items)]) + b"".join(self._item_spec(i, as_bit=False) for i in items))
        results: list[bytes | S7Error] = []
        pos = 0
        for n, _item in enumerate(items):
            if pos + 4 > len(data):
                results.append(S7Error("the PLC returned no data for this item"))
                continue
            code, transport, length = data[pos], data[pos + 1], struct.unpack(">H", data[pos + 2:pos + 4])[0]
            pos += 4
            if code != 0xFF:
                results.append(S7Error(ITEM_ERRORS.get(code, f"error 0x{code:02X}")))
                continue
            size = (length + 7) // 8 if transport in (3, 4, 5) else length
            results.append(data[pos:pos + size])
            pos += size + (size % 2 if n < len(items) - 1 else 0)
        return results

    def write(self, item: Item, value: bytes) -> None:
        if item.is_timer_or_counter:
            payload = bytes([0, 9, 0, 2]) + value          # one 2-byte element, sent as an octet string
        elif item.bit is not None:
            payload = bytes([0, 3, 0, 1, 1 if value[0] else 0])
        else:
            payload = bytes([0, 4]) + struct.pack(">H", len(value) * 8) + value
        _, data = self._job(bytes([0x05, 1]) + self._item_spec(item, as_bit=item.bit is not None), payload)
        if data[0] != 0xFF:
            raise S7Error(ITEM_ERRORS.get(data[0], f"error 0x{data[0]:02X}"))

    def read_szl(self, szl_id: int, index: int = 0) -> list[bytes]:
        """Read a system status list; returns its data records."""
        param = bytes([0, 1, 0x12, 4, 0x11, 0x44, 1, 0])
        data = bytes([0xFF, 9, 0, 4]) + struct.pack(">HH", szl_id, index)
        payload = b""
        while True:
            header = struct.pack(">BBHHHH", 0x32, 7, 0, self._next_ref(), len(param), len(data))
            reply = self._transport.exchange(header + param + data)
            if len(reply) < 10 or reply[0] != 0x32 or reply[1] != 7:
                raise S7Error("Unexpected answer from the PLC.")
            param_len = struct.unpack(">H", reply[6:8])[0]
            reply_param, reply_data = reply[10:10 + param_len], reply[10 + param_len:]
            error = struct.unpack(">H", reply_param[10:12])[0] if len(reply_param) >= 12 else 0
            if error or reply_data[0] != 0xFF:
                raise S7Error(f"The PLC does not provide status list 0x{szl_id:04X} (error 0x{error:04X}).")
            payload += reply_data[4:]
            if len(reply_param) < 12 or reply_param[9] == 0:
                break
            # More fragments follow: ask for the next one with the sequence number the PLC handed out.
            param = bytes([0, 1, 0x12, 8, 0x12, 0x44, 1, reply_param[7], 0, 0, 0, 0])
            data = bytes([0x0A, 0, 0, 0])
        record_len, count = struct.unpack(">HH", payload[4:8])
        return [payload[8 + i * record_len:8 + (i + 1) * record_len] for i in range(count)]
