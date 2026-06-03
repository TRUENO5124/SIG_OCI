"""MCU simulator for the STM32F103 signal generator + oscilloscope project.

The simulator listens on 127.0.0.1:7897.

It supports two clients on the same port:

- Raw TCP line protocol, used by host_app.py.
- WebSocket line protocol, used by instrument_panel.html in a browser.
"""

from __future__ import annotations

import base64
import hashlib
import math
import random
import socket
import socketserver
import struct
import threading
import time
from dataclasses import dataclass

HOST = "127.0.0.1"
PORT = 7897
VREF_MV = 3300
CAP_MAX_RATE_HZ = 51200
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


@dataclass
class GeneratorState:
    wave: str = "OFF"
    freq_hz: float = 1000.0
    amp_percent: float = 50.0
    offset_percent: float = 50.0
    started_at: float = time.monotonic()


class SimState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.generator = GeneratorState()

    def set_generator(self, wave: str, freq_hz: float, amp: float, offset: float) -> None:
        with self.lock:
            self.generator = GeneratorState(wave, freq_hz, amp, offset, time.monotonic())

    def snapshot(self) -> GeneratorState:
        with self.lock:
            return GeneratorState(
                self.generator.wave,
                self.generator.freq_hz,
                self.generator.amp_percent,
                self.generator.offset_percent,
                self.generator.started_at,
            )


STATE = SimState()


def wave_value(state: GeneratorState, t: float) -> float:
    if state.wave == "OFF":
        return 0.0

    phase = ((t - state.started_at) * state.freq_hz) % 1.0
    if state.wave == "SINE":
        raw = 0.5 + 0.5 * math.sin(2.0 * math.pi * phase)
    elif state.wave == "SQUARE":
        raw = 1.0 if phase < 0.5 else 0.0
    elif state.wave == "TRIANGLE":
        raw = 2.0 * phase if phase < 0.5 else 2.0 * (1.0 - phase)
    elif state.wave == "SAW":
        raw = phase
    else:
        raw = 0.0

    duty_percent = state.offset_percent + (raw - 0.5) * state.amp_percent
    duty_percent = max(0.0, min(100.0, duty_percent))
    return duty_percent / 100.0


def capture_samples(samples: int, rate_hz: int) -> list[int]:
    state = STATE.snapshot()
    now = time.monotonic()
    values: list[int] = []
    for i in range(samples):
        t = now + i / float(rate_hz)
        value = wave_value(state, t)
        noise = random.uniform(-0.006, 0.006)
        adc = round(max(0.0, min(1.0, value + noise)) * 4095.0)
        values.append(adc)
    return values


class ProtocolSession:
    def send_line(self, text: str) -> None:
        raise NotImplementedError

    def handle_command(self, line: str) -> None:
        parts = line.split()
        if not parts:
            return

        cmd = parts[0].upper()

        if cmd == "PING":
            self.send_line("PONG STM32F103_SIGSCOPE")
        elif cmd == "INFO":
            self.send_line(
                "INFO MCU=STM32F103C8T6_SIM UART=PORT_7897 "
                "GEN=PA6_TIM3CH1_PWM SCOPE=PA0_ADC1IN0 GEN_HZ=1-10000 "
                f"CAP_MAX=512 CAP_MAX_RATE={CAP_MAX_RATE_HZ} PORT=7897_SIM"
            )
        elif cmd == "GEN":
            self.handle_gen(parts)
        elif cmd in {"CAP", "CAPTURE"}:
            self.handle_capture(parts)
        elif cmd == "HELP":
            self.send_line("CMDS PING INFO GEN OFF GEN <WAVE> <HZ> <AMP%> <OFFSET%> CAP <N> <RATE>")
        else:
            self.send_line("ERR BAD_CMD")

    def handle_gen(self, parts: list[str]) -> None:
        if len(parts) >= 2 and parts[1].upper() == "OFF":
            STATE.set_generator("OFF", 0.0, 0.0, 50.0)
            self.send_line("OK GEN OFF")
            return

        if len(parts) != 5:
            self.send_line("ERR GEN_USAGE GEN <SINE|SQUARE|TRIANGLE|SAW> <HZ> <AMP%> <OFFSET%>")
            return

        wave = parts[1].upper()
        if wave not in {"SINE", "SQUARE", "TRIANGLE", "SAW"}:
            self.send_line("ERR GEN_USAGE GEN <SINE|SQUARE|TRIANGLE|SAW> <HZ> <AMP%> <OFFSET%>")
            return

        try:
            freq = float(parts[2])
            amp = float(parts[3])
            offset = float(parts[4])
        except ValueError:
            self.send_line("ERR GEN_RANGE")
            return

        if not (1.0 <= freq <= 10000.0 and 0.0 <= amp <= 100.0 and 0.0 <= offset <= 100.0):
            self.send_line("ERR GEN_RANGE")
            return

        STATE.set_generator(wave, freq, amp, offset)
        self.send_line(f"OK GEN {wave} {int(freq)} {int(amp)} {int(offset)}")

    def handle_capture(self, parts: list[str]) -> None:
        if len(parts) != 3:
            self.send_line("ERR CAP_USAGE CAP <SAMPLES> <RATE_HZ>")
            return

        try:
            samples = int(parts[1])
            rate_hz = int(parts[2])
        except ValueError:
            self.send_line("ERR CAP_RANGE")
            return

        if not (1 <= samples <= 512 and 10 <= rate_hz <= CAP_MAX_RATE_HZ):
            self.send_line("ERR CAP_RANGE")
            return

        values = capture_samples(samples, rate_hz)
        self.send_line(f"DATA {samples} {rate_hz} {VREF_MV}")
        for start in range(0, len(values), 16):
            self.send_line(",".join(str(v) for v in values[start : start + 16]))
        self.send_line("END")


class RequestHandler(socketserver.BaseRequestHandler, ProtocolSession):
    def handle(self) -> None:
        self.request.settimeout(8.0)
        first = self._recv_until_header_or_line()
        if not first:
            return

        if first.startswith(b"GET ") and b"Upgrade: websocket" in first:
            self._handle_websocket(first)
        else:
            self._handle_raw_tcp(first)

    def send_line(self, text: str) -> None:
        self.request.sendall((text + "\r\n").encode("ascii"))

    def _recv_until_header_or_line(self) -> bytes:
        data = bytearray()
        while True:
            chunk = self.request.recv(1)
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if data.startswith(b"GET "):
                if data.endswith(b"\r\n\r\n"):
                    return bytes(data)
            elif data.endswith(b"\n"):
                return bytes(data)
            if len(data) > 4096:
                return bytes(data)

    def _handle_raw_tcp(self, first: bytes) -> None:
        self.send_line("READY STM32F103_SIGSCOPE_SIM")
        self.send_line("PINS GEN=PA6_PWM SCOPE=PA0_ADC UART=TCP_7897")

        buffer = bytearray(first)
        while True:
            while b"\n" in buffer:
                raw_line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                line = raw_line.decode("ascii", errors="ignore").strip()
                if line:
                    try:
                        self.handle_command(line)
                    except Exception as exc:
                        self.send_line(f"ERR SIM_EXCEPTION {exc}")
            chunk = self.request.recv(512)
            if not chunk:
                return
            buffer.extend(chunk)

    def _handle_websocket(self, header: bytes) -> None:
        headers = self._parse_headers(header)
        key = headers.get("sec-websocket-key")
        if not key:
            return

        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        self.request.sendall(response.encode("ascii"))
        ws = WebSocketPeer(self.request)
        ws.send_text("READY STM32F103_SIGSCOPE_SIM")
        ws.send_text("PINS GEN=PA6_PWM SCOPE=PA0_ADC UART=WS_7897")

        session = WebSocketSession(ws)
        while True:
            message = ws.recv_text()
            if message is None:
                return
            for line in message.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    session.handle_command(line)
                except Exception as exc:
                    session.send_line(f"ERR SIM_EXCEPTION {exc}")

    @staticmethod
    def _parse_headers(header: bytes) -> dict[str, str]:
        lines = header.decode("ascii", errors="ignore").split("\r\n")
        result: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                result[key.strip().lower()] = value.strip()
        return result


class WebSocketSession(ProtocolSession):
    def __init__(self, ws: "WebSocketPeer") -> None:
        self.ws = ws

    def send_line(self, text: str) -> None:
        self.ws.send_text(text)


class WebSocketPeer:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    def recv_text(self) -> str | None:
        header = self._recv_exact(2)
        if not header:
            return None

        first, second = header
        opcode = first & 0x0F
        masked = (second & 0x80) != 0
        length = second & 0x7F

        if length == 126:
            ext = self._recv_exact(2)
            if not ext:
                return None
            length = struct.unpack("!H", ext)[0]
        elif length == 127:
            ext = self._recv_exact(8)
            if not ext:
                return None
            length = struct.unpack("!Q", ext)[0]

        mask = b""
        if masked:
            mask = self._recv_exact(4)
            if not mask:
                return None

        payload = self._recv_exact(length)
        if payload is None:
            return None

        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

        if opcode == 0x8:
            return None
        if opcode == 0x9:
            self._send_frame(payload, opcode=0xA)
            return ""
        if opcode != 0x1:
            return ""

        return payload.decode("utf-8", errors="ignore")

    def send_text(self, text: str) -> None:
        self._send_frame(text.encode("utf-8"), opcode=0x1)

    def _send_frame(self, payload: bytes, opcode: int) -> None:
        first = 0x80 | opcode
        length = len(payload)
        if length < 126:
            header = bytes([first, length])
        elif length <= 0xFFFF:
            header = bytes([first, 126]) + struct.pack("!H", length)
        else:
            header = bytes([first, 127]) + struct.pack("!Q", length)
        self.sock.sendall(header + payload)

    def _recv_exact(self, length: int) -> bytes | None:
        data = bytearray()
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    with ThreadingTCPServer((HOST, PORT), RequestHandler) as server:
        print(f"MCU simulator listening on {HOST}:{PORT}")
        print("Raw TCP: PING, INFO, GEN <wave> <hz> <amp> <offset>, CAP <samples> <rate>")
        print("Browser: open instrument_panel.html and connect to ws://127.0.0.1:7897")
        server.serve_forever()


if __name__ == "__main__":
    main()
