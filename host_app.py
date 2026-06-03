"""PC host software for the STM32F103 signal generator + oscilloscope project."""

from __future__ import annotations

import queue
import socket
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

try:
    import serial  # type: ignore
    import serial.tools.list_ports  # type: ignore
except Exception:  # pyserial is optional; TCP simulator works without it.
    serial = None


TCP_HOST = "127.0.0.1"
TCP_PORT = 7897
DEFAULT_BAUD = 115200


class TransportError(RuntimeError):
    pass


class LineTransport:
    def readline(self, timeout: float = 2.0) -> str:
        raise NotImplementedError

    def write_line(self, line: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class TcpTransport(LineTransport):
    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.create_connection((host, port), timeout=2.0)
        self.sock.settimeout(0.2)
        self.buffer = bytearray()

    def readline(self, timeout: float = 2.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer = bytearray(rest)
                return line.decode("ascii", errors="replace").strip()
            try:
                data = self.sock.recv(256)
            except socket.timeout:
                continue
            if not data:
                raise TransportError("TCP connection closed")
            self.buffer.extend(data)
        raise TransportError("Read timeout")

    def write_line(self, line: str) -> None:
        self.sock.sendall((line + "\n").encode("ascii"))

    def close(self) -> None:
        self.sock.close()


class SerialTransport(LineTransport):
    def __init__(self, port: str, baud: int = DEFAULT_BAUD) -> None:
        if serial is None:
            raise TransportError("pyserial is not installed")
        self.ser = serial.Serial(port, baudrate=baud, timeout=0.2)

    def readline(self, timeout: float = 2.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.ser.readline()
            if data:
                return data.decode("ascii", errors="replace").strip()
        raise TransportError("Read timeout")

    def write_line(self, line: str) -> None:
        self.ser.write((line + "\n").encode("ascii"))

    def close(self) -> None:
        self.ser.close()


@dataclass
class CaptureResult:
    samples: list[int]
    sample_rate_hz: int
    vref_mv: int


class DeviceClient:
    def __init__(self, transport: LineTransport, log_queue: queue.Queue[str]) -> None:
        self.transport = transport
        self.log_queue = log_queue
        self.lock = threading.Lock()
        self._drain_ready_lines()

    def close(self) -> None:
        self.transport.close()

    def _log(self, text: str) -> None:
        self.log_queue.put(text)

    def _drain_ready_lines(self) -> None:
        for _ in range(4):
            try:
                line = self.transport.readline(timeout=0.25)
            except Exception:
                return
            if line:
                self._log(f"< {line}")

    def command(self, line: str, timeout: float = 2.0) -> str:
        with self.lock:
            self._log(f"> {line}")
            self.transport.write_line(line)
            response = self.transport.readline(timeout=timeout)
            self._log(f"< {response}")
            return response

    def set_generator(self, wave: str, freq: int, amp: int, offset: int) -> str:
        return self.command(f"GEN {wave} {freq} {amp} {offset}")

    def stop_generator(self) -> str:
        return self.command("GEN OFF")

    def ping(self) -> str:
        return self.command("PING")

    def info(self) -> str:
        return self.command("INFO")

    def capture(self, samples: int, sample_rate_hz: int) -> CaptureResult:
        with self.lock:
            cmd = f"CAP {samples} {sample_rate_hz}"
            self._log(f"> {cmd}")
            self.transport.write_line(cmd)
            header = self.transport.readline(timeout=5.0)
            self._log(f"< {header}")

            parts = header.split()
            if len(parts) != 4 or parts[0] != "DATA":
                raise TransportError(header)

            expected = int(parts[1])
            rate = int(parts[2])
            vref = int(parts[3])
            values: list[int] = []

            while True:
                line = self.transport.readline(timeout=5.0)
                self._log(f"< {line}")
                if line == "END":
                    break
                for item in line.split(","):
                    item = item.strip()
                    if item:
                        values.append(int(item))

            if len(values) != expected:
                raise TransportError(f"Expected {expected} samples, got {len(values)}")

            return CaptureResult(values, rate, vref)


class ScopeCanvas(tk.Canvas):
    def __init__(self, master: tk.Widget, **kwargs: object) -> None:
        super().__init__(master, background="#101418", highlightthickness=0, **kwargs)
        self.samples: list[int] = []
        self.vref_mv = 3300
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_data(self, samples: list[int], vref_mv: int) -> None:
        self.samples = samples
        self.vref_mv = vref_mv
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())

        for i in range(1, 5):
            y = height * i / 5
            self.create_line(0, y, width, y, fill="#24313a")
        for i in range(1, 10):
            x = width * i / 10
            self.create_line(x, 0, x, height, fill="#1b252c")

        self.create_text(
            10,
            10,
            anchor="nw",
            fill="#9db2bf",
            text=f"{len(self.samples)} samples, Vref {self.vref_mv} mV",
            font=("Segoe UI", 9),
        )

        if len(self.samples) < 2:
            self.create_text(
                width / 2,
                height / 2,
                fill="#6d7f8b",
                text="No capture yet",
                font=("Segoe UI", 12),
            )
            return

        points: list[float] = []
        for idx, value in enumerate(self.samples):
            x = idx * (width - 1) / (len(self.samples) - 1)
            y = height - (value / 4095.0) * (height - 24) - 12
            points.extend([x, y])

        self.create_line(*points, fill="#4bd4a4", width=2, smooth=False)

        mn = min(self.samples)
        mx = max(self.samples)
        avg = sum(self.samples) / len(self.samples)
        text = f"min {mn}  max {mx}  avg {avg:.0f}  Vpp {(mx - mn) * self.vref_mv / 4095:.0f} mV"
        self.create_text(width - 10, 10, anchor="ne", fill="#d4e2ea", text=text, font=("Segoe UI", 9))


class HostApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("STM32F103 Signal Generator + Oscilloscope")
        self.geometry("980x650")
        self.minsize(860, 560)

        self.client: DeviceClient | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.connection_mode = tk.StringVar(value="TCP simulator")
        self.serial_port = tk.StringVar(value="")
        self.wave = tk.StringVar(value="SINE")
        self.freq = tk.IntVar(value=1000)
        self.amp = tk.IntVar(value=70)
        self.offset = tk.IntVar(value=50)
        self.samples = tk.IntVar(value=256)
        self.sample_rate = tk.IntVar(value=5000)
        self.status = tk.StringVar(value="Disconnected")

        self._build_ui()
        self._refresh_ports()
        self.after(100, self._pump_log)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("TButton", padding=(10, 6))
        style.configure("TLabel", padding=(0, 2))

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        controls = ttk.Frame(root, width=280)
        controls.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        controls.grid_propagate(False)

        ttk.Label(controls, text="Connection", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Combobox(
            controls,
            textvariable=self.connection_mode,
            values=["TCP simulator", "Serial board"],
            state="readonly",
        ).pack(fill="x", pady=(4, 6))

        port_row = ttk.Frame(controls)
        port_row.pack(fill="x")
        self.port_combo = ttk.Combobox(port_row, textvariable=self.serial_port, values=[], width=18)
        self.port_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(port_row, text="Refresh", command=self._refresh_ports).pack(side="left", padx=(6, 0))

        conn_row = ttk.Frame(controls)
        conn_row.pack(fill="x", pady=(8, 16))
        ttk.Button(conn_row, text="Connect", command=self.connect).pack(side="left", expand=True, fill="x")
        ttk.Button(conn_row, text="Close", command=self.disconnect).pack(side="left", expand=True, fill="x", padx=(6, 0))

        ttk.Label(controls, text="Signal Generator", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(controls, text="Wave").pack(anchor="w")
        ttk.Combobox(
            controls,
            textvariable=self.wave,
            values=["SINE", "SQUARE", "TRIANGLE", "SAW"],
            state="readonly",
        ).pack(fill="x", pady=(0, 8))

        self._labeled_spinbox(controls, "Frequency (Hz)", self.freq, 1, 10000, 1)
        self._labeled_scale(controls, "Amplitude (%)", self.amp)
        self._labeled_scale(controls, "Offset (%)", self.offset)

        gen_row = ttk.Frame(controls)
        gen_row.pack(fill="x", pady=(8, 16))
        ttk.Button(gen_row, text="Apply", command=self.apply_generator).pack(side="left", expand=True, fill="x")
        ttk.Button(gen_row, text="Stop", command=self.stop_generator).pack(side="left", expand=True, fill="x", padx=(6, 0))

        ttk.Label(controls, text="Oscilloscope", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 4))
        self._labeled_spinbox(controls, "Samples", self.samples, 1, 512, 1)
        self._labeled_spinbox(controls, "Sample Rate (Hz)", self.sample_rate, 10, 51200, 10)
        ttk.Button(controls, text="Capture", command=self.capture).pack(fill="x", pady=(8, 14))

        ttk.Button(controls, text="Ping", command=self.ping).pack(fill="x")
        ttk.Button(controls, text="Read Info", command=self.info).pack(fill="x", pady=(6, 0))

        ttk.Label(controls, textvariable=self.status, foreground="#266a4f").pack(anchor="w", pady=(14, 0))

        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=0)
        right.columnconfigure(0, weight=1)

        self.scope = ScopeCanvas(right, height=380)
        self.scope.grid(row=0, column=0, sticky="nsew")

        log_frame = ttk.Frame(right)
        log_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        ttk.Label(log_frame, text="Protocol Log", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(log_frame, height=9, wrap="word", font=("Consolas", 9))
        self.log_text.grid(row=1, column=0, sticky="ew")

    def _labeled_scale(self, parent: ttk.Frame, label: str, variable: tk.IntVar) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 6))
        ttk.Label(row, text=label).pack(anchor="w")
        scale = ttk.Scale(row, from_=0, to=100, variable=variable, orient="horizontal")
        scale.pack(side="left", fill="x", expand=True)
        ttk.Label(row, textvariable=variable, width=4).pack(side="left", padx=(8, 0))

    def _labeled_spinbox(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.IntVar,
        start: int,
        end: int,
        increment: int,
    ) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 6))
        ttk.Label(row, text=label).pack(anchor="w")
        ttk.Spinbox(row, from_=start, to=end, increment=increment, textvariable=variable).pack(fill="x")

    def _refresh_ports(self) -> None:
        if serial is None:
            ports: list[str] = []
        else:
            ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo.configure(values=ports)
        if ports and not self.serial_port.get():
            self.serial_port.set(ports[0])

    def _run_worker(self, title: str, fn: object) -> None:
        def worker() -> None:
            try:
                fn()  # type: ignore[misc]
            except Exception as exc:
                self.log_queue.put(f"! {title}: {exc}")
                self.after(0, lambda: messagebox.showerror(title, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def connect(self) -> None:
        def work() -> None:
            self.disconnect(silent=True)
            if self.connection_mode.get() == "TCP simulator":
                transport: LineTransport = TcpTransport(TCP_HOST, TCP_PORT)
                label = f"Connected to simulator {TCP_HOST}:{TCP_PORT}"
            else:
                port = self.serial_port.get().strip()
                if not port:
                    raise TransportError("Select a serial port first")
                transport = SerialTransport(port)
                label = f"Connected to serial {port} @ {DEFAULT_BAUD}"
            self.client = DeviceClient(transport, self.log_queue)
            self.after(0, lambda: self.status.set(label))

        self._run_worker("Connect", work)

    def disconnect(self, silent: bool = False) -> None:
        client = self.client
        self.client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if not silent:
            self.status.set("Disconnected")

    def require_client(self) -> DeviceClient:
        if self.client is None:
            raise TransportError("Connect first")
        return self.client

    def ping(self) -> None:
        self._run_worker("Ping", lambda: self.require_client().ping())

    def info(self) -> None:
        self._run_worker("Read Info", lambda: self.require_client().info())

    def apply_generator(self) -> None:
        def work() -> None:
            self.require_client().set_generator(
                self.wave.get(),
                int(self.freq.get()),
                int(self.amp.get()),
                int(self.offset.get()),
            )

        self._run_worker("Apply Generator", work)

    def stop_generator(self) -> None:
        self._run_worker("Stop Generator", lambda: self.require_client().stop_generator())

    def capture(self) -> None:
        def work() -> None:
            result = self.require_client().capture(int(self.samples.get()), int(self.sample_rate.get()))
            self.after(0, lambda: self.scope.set_data(result.samples, result.vref_mv))

        self._run_worker("Capture", work)

    def _pump_log(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
        self.after(100, self._pump_log)


def main() -> None:
    app = HostApp()
    app.mainloop()


if __name__ == "__main__":
    main()
