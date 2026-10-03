"""ISO-on-TCP gateway to STEP 7's S7ONLINE access point.

S7-PLCSIM (classic) has no network port: it is only reachable through STEP 7's
PG/PC interface driver. This gateway listens on a local TCP port, speaks
ISO-on-TCP (RFC 1006) to any S7 client such as python-snap7, and forwards each
S7 message through bridge/s7online_link.ps1. With PLCSIM running the messages
go to the simulator; otherwise they go to the PLC reached by the configured
PG/PC interface.

Standalone use, for other S7 client programs:
    python s7online_gateway.py --address 2 --port 1102
then connect the client to 127.0.0.1, port 1102.
"""

from __future__ import annotations

import argparse
import socket
import threading

from worker import Bridge, BridgeError

COTP_CONNECT_REQUEST = 0xE0
COTP_CONNECT_CONFIRM = 0xD0
COTP_DATA = 0xF0
COTP_END_OF_MESSAGE = 0x80


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("client closed the connection")
        data += chunk
    return data


def _recv_tpkt(sock: socket.socket) -> bytes:
    header = _recv_exact(sock, 4)
    if header[0] != 3:
        raise ConnectionError("not a TPKT frame")
    return _recv_exact(sock, int.from_bytes(header[2:4], "big") - 4)


def _send_tpkt(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(b"\x03\x00" + (len(payload) + 4).to_bytes(2, "big") + payload)


class S7OnlineGateway:
    """Serves one PLC address; one client connection at a time."""

    def __init__(self, address: str, rack: int = 0, slot: int = 2, port: int = 0, host: str = "127.0.0.1") -> None:
        self.address, self.rack, self.slot = address, rack, slot
        self._link = Bridge("s7online_link.ps1", label="S7ONLINE")
        self._server = socket.create_server((host, port))
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.close()
        self._link.stop()

    def _accept_loop(self) -> None:
        while True:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            with client:
                try:
                    self._serve(client)
                except (ConnectionError, OSError, BridgeError):
                    pass
                finally:
                    try:
                        self._link.call("close", timeout=15)
                    except BridgeError:
                        pass

    def _serve(self, client: socket.socket) -> None:
        request = _recv_tpkt(client)
        if len(request) < 7 or request[1] != COTP_CONNECT_REQUEST:
            raise ConnectionError("expected a COTP connection request")
        # A failed open closes the socket before the confirm, which the client reports as a refused connection.
        self._link.call("open", timeout=30, address=self.address, rack=self.rack, slot=self.slot)
        params = request[7:]
        confirm = bytes([COTP_CONNECT_CONFIRM]) + request[4:6] + b"\x00\x01\x00" + params
        _send_tpkt(client, bytes([len(confirm)]) + confirm)

        pdu = b""
        while True:
            frame = _recv_tpkt(client)
            if len(frame) < 3 or frame[1] != COTP_DATA:
                continue
            pdu += frame[frame[0] + 1:]
            if not frame[2] & COTP_END_OF_MESSAGE:
                continue
            reply = self._link.call("exchange", timeout=30, pdu=pdu.hex())
            pdu = b""
            _send_tpkt(client, bytes([2, COTP_DATA, COTP_END_OF_MESSAGE]) + bytes.fromhex(reply["pdu"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="ISO-on-TCP gateway to STEP 7's S7ONLINE access point (S7-PLCSIM).")
    parser.add_argument("--address", default="2", help="MPI/DP station number or IP address of the CPU (default 2)")
    parser.add_argument("--rack", type=int, default=0)
    parser.add_argument("--slot", type=int, default=2)
    parser.add_argument("--port", type=int, default=1102, help="local TCP port to listen on (default 1102)")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    gateway = S7OnlineGateway(args.address, args.rack, args.slot, port=args.port, host=args.host)
    print(f"Gateway for CPU address {args.address} listening on {args.host}:{gateway.port}. Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        gateway.close()


if __name__ == "__main__":
    main()
