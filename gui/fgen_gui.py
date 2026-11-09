# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5"]
# ///
"""Tk GUI for the Arduino square-wave function generator (firmware/fgen)."""

import math
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk

import serial
from serial.tools import list_ports

BAUD = 115200
F_MIN = 0.25  # Hz, just above the Timer1 floor of ~0.238 Hz
F_MAX = 1_000_000.0  # Hz; the timer goes to 8 MHz but duty resolution collapses
SEND_DELAY_MS = 60  # debounce for slider drags

STATUS_RE = re.compile(
    r"OK F=(?P<f>[\d.]+) D=(?P<d>[\d.]+) P=(?P<p>\d+) T=(?P<t>\d+) E=(?P<e>[01])"
)
SUFFIXES = {"": 1, "k": 1e3, "m": 1e6}


def parse_frequency(text: str) -> float:
    """Parse '1500', '1.5k', '2M', '440 Hz' into Hz."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([kKmM]?)\s*(?:[hH][zZ])?\s*", text)
    if not m:
        raise ValueError(f"not a frequency: {text!r}")
    return float(m.group(1)) * SUFFIXES[m.group(2).lower()]


def format_frequency(hz: float) -> str:
    if hz >= 1e6:
        return f"{hz / 1e6:.6g} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.6g} kHz"
    return f"{hz:.6g} Hz"


def format_time(seconds: float) -> str:
    for unit, scale in (("s", 1), ("ms", 1e-3), ("µs", 1e-6)):
        if seconds >= scale:
            return f"{seconds / scale:.4g} {unit}"
    return f"{seconds / 1e-9:.4g} ns"


class Device:
    """Serial link to the firmware; received lines land in `self.lines`."""

    def __init__(self, port: str):
        self.ser = serial.Serial(port, BAUD, timeout=0.1)
        self.lines: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                raw = self.ser.readline()
            except (serial.SerialException, OSError, TypeError):
                self.lines.put("DISCONNECTED")
                return
            if raw:
                self.lines.put(raw.decode(errors="replace").strip())

    def send(self, command: str):
        self.ser.write((command + "\n").encode())

    def close(self):
        self._stop.set()
        self.ser.close()


class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=12)
        self.root = root
        self.device: Device | None = None
        self._pending: dict[str, str] = {}
        self._send_job = None
        self._syncing = False  # suppress callbacks while updating widgets

        self.port_var = tk.StringVar()
        self.freq_text = tk.StringVar(value="1k")
        self.freq_slider = tk.DoubleVar(value=math.log10(1000))
        self.duty_var = tk.DoubleVar(value=50.0)
        self.duty_text = tk.StringVar(value="50")
        self.enabled_var = tk.BooleanVar(value=True)
        self.conn_status = tk.StringVar(value="Not connected")

        self.actual = {
            key: tk.StringVar(value="—")
            for key in ("freq", "duty", "period", "high", "steps", "prescaler")
        }

        self._build()
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.refresh_ports()
        self._set_controls_state(False)
        self.after(50, self._poll)

    # ---------- layout ----------

    def _build(self):
        self.columnconfigure(0, weight=1)

        conn = ttk.LabelFrame(self, text="Connection", padding=8)
        conn.grid(row=0, column=0, sticky="ew")
        conn.columnconfigure(0, weight=1)
        self.port_box = ttk.Combobox(conn, textvariable=self.port_var, width=30)
        self.port_box.grid(row=0, column=0, sticky="ew")
        ttk.Button(conn, text="Refresh", command=self.refresh_ports).grid(
            row=0, column=1, padx=4
        )
        self.connect_btn = ttk.Button(conn, text="Connect", command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=2)
        ttk.Label(conn, textvariable=self.conn_status, foreground="gray").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )

        # Frequency
        fr = ttk.LabelFrame(self, text="Frequency", padding=8)
        fr.grid(row=1, column=0, sticky="ew", pady=8)
        fr.columnconfigure(0, weight=1)
        self.freq_scale = ttk.Scale(
            fr,
            from_=math.log10(F_MIN),
            to=math.log10(F_MAX),
            variable=self.freq_slider,
            command=self._on_freq_slider,
        )
        self.freq_scale.grid(row=0, column=0, sticky="ew")
        self.freq_entry = ttk.Entry(fr, textvariable=self.freq_text, width=10)
        self.freq_entry.grid(row=0, column=1, padx=4)
        self.freq_entry.bind("<Return>", self._on_freq_entry)
        self.freq_entry.bind("<FocusOut>", self._on_freq_entry)
        ttk.Label(fr, text="Hz  (k / M ok)").grid(row=0, column=2)
        presets = ttk.Frame(fr)
        presets.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.freq_buttons = []
        for label, hz in (
            ("1 Hz", 1), ("10 Hz", 10), ("100 Hz", 100), ("1 kHz", 1e3),
            ("10 kHz", 1e4), ("100 kHz", 1e5), ("1 MHz", 1e6),
        ):
            b = ttk.Button(presets, text=label, width=7,
                           command=lambda hz=hz: self.set_frequency(hz))
            b.pack(side="left", padx=1)
            self.freq_buttons.append(b)

        # Duty cycle
        du = ttk.LabelFrame(self, text="Duty cycle", padding=8)
        du.grid(row=2, column=0, sticky="ew")
        du.columnconfigure(0, weight=1)
        self.duty_scale = ttk.Scale(
            du, from_=0, to=100, variable=self.duty_var, command=self._on_duty_slider
        )
        self.duty_scale.grid(row=0, column=0, sticky="ew")
        self.duty_entry = ttk.Entry(du, textvariable=self.duty_text, width=10)
        self.duty_entry.grid(row=0, column=1, padx=4)
        self.duty_entry.bind("<Return>", self._on_duty_entry)
        self.duty_entry.bind("<FocusOut>", self._on_duty_entry)
        ttk.Label(du, text="%").grid(row=0, column=2, sticky="w")
        presets = ttk.Frame(du)
        presets.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.duty_buttons = []
        for pct in (10, 25, 50, 75, 90):
            b = ttk.Button(presets, text=f"{pct}%", width=7,
                           command=lambda p=pct: self.set_duty(p))
            b.pack(side="left", padx=1)
            self.duty_buttons.append(b)

        # Output + readout
        out = ttk.LabelFrame(self, text="Output (pin 9)", padding=8)
        out.grid(row=3, column=0, sticky="ew", pady=8)
        out.columnconfigure(1, weight=1)
        self.enable_check = ttk.Checkbutton(
            out, text="Output enabled", variable=self.enabled_var,
            command=lambda: self._queue("E", "1" if self.enabled_var.get() else "0"),
        )
        self.enable_check.grid(row=0, column=0, columnspan=2, sticky="w")

        rows = (
            ("Actual frequency", "freq"),
            ("Actual duty", "duty"),
            ("Period", "period"),
            ("High time", "high"),
            ("Duty resolution", "steps"),
            ("Timer prescaler", "prescaler"),
        )
        big = ("TkDefaultFont", 14, "bold")
        for i, (label, key) in enumerate(rows, start=1):
            ttk.Label(out, text=label + ":").grid(row=i, column=0, sticky="w")
            ttk.Label(out, textvariable=self.actual[key],
                      font=big if i <= 2 else None).grid(row=i, column=1, sticky="w", padx=8)

        self.wave = tk.Canvas(self, height=90, background="#111", highlightthickness=0)
        self.wave.grid(row=4, column=0, sticky="ew")
        self.wave.bind("<Configure>", lambda e: self._draw_wave())
        self._wave_duty: float | None = None
        self._wave_on = False

    # ---------- connection ----------

    def refresh_ports(self):
        ports = list_ports.comports()
        names = [p.device for p in ports]
        self.port_box["values"] = names
        if names and self.port_var.get() not in names:
            # Prefer something that looks like an Arduino.
            arduino = [p.device for p in ports
                       if "arduino" in (p.manufacturer or p.description or "").lower()
                       or p.device.startswith("/dev/ttyACM")]
            self.port_var.set((arduino or names)[0])

    def toggle_connection(self):
        if self.device:
            self._disconnect("Disconnected")
            return
        port = self.port_var.get()
        if not port:
            self.conn_status.set("Pick a serial port first")
            return
        try:
            self.device = Device(port)
        except (serial.SerialException, OSError) as exc:
            self.conn_status.set(f"Could not open {port}: {exc}")
            return
        # Opening the port resets the Uno; the firmware prints READY after boot.
        self.conn_status.set(f"Opened {port}, waiting for board to boot…")
        self.connect_btn.config(text="Disconnect")
        self._connected_at = time.monotonic()

    def _disconnect(self, message: str):
        if self.device:
            self.device.close()
            self.device = None
        self.connect_btn.config(text="Connect")
        self.conn_status.set(message)
        self._set_controls_state(False)
        for var in self.actual.values():
            var.set("—")
        self._wave_on = False
        self._draw_wave()

    def _set_controls_state(self, on: bool):
        state = ["!disabled"] if on else ["disabled"]
        for w in (self.freq_scale, self.freq_entry, self.duty_scale, self.duty_entry,
                  self.enable_check, *self.freq_buttons, *self.duty_buttons):
            w.state(state)

    def _poll(self):
        if self.device:
            try:
                while True:
                    self._handle_line(self.device.lines.get_nowait())
            except queue.Empty:
                pass
            if (self.device and self.conn_status.get().startswith("Opened")
                    and time.monotonic() - self._connected_at > 4):
                self.conn_status.set("No reply — is the fgen firmware uploaded?")
        self.after(50, self._poll)

    def _handle_line(self, line: str):
        if line == "DISCONNECTED":
            self._disconnect("Board disconnected")
        elif line == "READY":
            self.conn_status.set(f"Connected to {self.device.ser.port}")
            self._set_controls_state(True)
            # Push the GUI's current settings so board and GUI agree.
            self._queue("F", repr(self._current_frequency()))
            self._queue("D", repr(self.duty_var.get()))
            self._queue("E", "1" if self.enabled_var.get() else "0")
        elif m := STATUS_RE.fullmatch(line):
            self._show_status(m)
        elif line.startswith("ERR"):
            self.conn_status.set(f"Board says: {line[4:]}")

    # ---------- sending ----------

    def _queue(self, op: str, value: str):
        """Coalesce rapid changes and send only the latest value per command."""
        if not self.device:
            return
        self._pending[op] = value
        if self._send_job is None:
            self._send_job = self.after(SEND_DELAY_MS, self._flush)

    def _flush(self):
        self._send_job = None
        if not self.device:
            self._pending.clear()
            return
        try:
            for op, value in self._pending.items():
                self.device.send(op + value)
        except (serial.SerialException, OSError):
            self._disconnect("Board disconnected")
        self._pending.clear()

    # ---------- frequency / duty handlers ----------

    def _current_frequency(self) -> float:
        return 10 ** self.freq_slider.get()

    def set_frequency(self, hz: float):
        hz = min(max(hz, F_MIN), F_MAX)
        self._syncing = True
        self.freq_slider.set(math.log10(hz))
        self._syncing = False
        self.freq_text.set(f"{hz:.6g}")
        self._queue("F", repr(hz))

    def _on_freq_slider(self, _value):
        if self._syncing:
            return
        # Snap to 3 significant figures so the slider lands on tidy values.
        hz = float(f"{self._current_frequency():.3g}")
        self.freq_text.set(f"{hz:.6g}")
        self._queue("F", repr(hz))

    def _on_freq_entry(self, _event=None):
        try:
            self.set_frequency(parse_frequency(self.freq_text.get()))
        except ValueError:
            self.freq_text.set(f"{self._current_frequency():.6g}")

    def set_duty(self, pct: float):
        pct = min(max(pct, 0.0), 100.0)
        self._syncing = True
        self.duty_var.set(pct)
        self._syncing = False
        self.duty_text.set(f"{pct:g}")
        self._queue("D", repr(pct))

    def _on_duty_slider(self, _value):
        if self._syncing:
            return
        pct = round(self.duty_var.get(), 1)
        self.duty_text.set(f"{pct:g}")
        self._queue("D", repr(pct))

    def _on_duty_entry(self, _event=None):
        try:
            self.set_duty(float(self.duty_text.get().strip().rstrip("%")))
        except ValueError:
            self.duty_text.set(f"{self.duty_var.get():g}")

    # ---------- readout ----------

    def _show_status(self, m: re.Match):
        freq, duty = float(m["f"]), float(m["d"])
        prescaler, top = int(m["p"]), int(m["t"])
        on = m["e"] == "1"
        period = 1 / freq
        self.actual["freq"].set(format_frequency(freq))
        self.actual["duty"].set(f"{duty:.2f} %" + ("" if on else "  (output off)"))
        self.actual["period"].set(format_time(period))
        self.actual["high"].set(format_time(period * duty / 100) if duty else "0")
        self.actual["steps"].set(f"{top + 1} steps ({100 / (top + 1):.3g} % each)")
        self.actual["prescaler"].set(f"/{prescaler}, TOP={top}")
        self._wave_duty, self._wave_on = duty, on
        self._draw_wave()

    def _draw_wave(self):
        c = self.wave
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10:
            return
        lo, hi = h - 15, 15
        if not self._wave_on or self._wave_duty is None:
            c.create_line(0, lo, w, lo, fill="#555", width=2)
            return
        if self._wave_duty in (0, 100):
            y = lo if self._wave_duty == 0 else hi
            c.create_line(0, y, w, y, fill="#4ade80", width=2)
            return
        cycles = 3
        cw = w / cycles
        pts = []
        for i in range(cycles):
            x0 = i * cw
            xh = x0 + cw * self._wave_duty / 100
            pts += [x0, lo, x0, hi, xh, hi, xh, lo, x0 + cw, lo]
        c.create_line(*pts, fill="#4ade80", width=2)


def main():
    root = tk.Tk()
    root.title("Arduino Function Generator")
    root.minsize(520, 0)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
