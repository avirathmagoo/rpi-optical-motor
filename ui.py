#!/usr/bin/env python3
"""
ui.py — Touchscreen UI for motor control
4 large buttons (Motor A Up/Down, Motor B Up/Down)
Center console showing all system status
No external libraries needed — pure tkinter
"""

import tkinter as tk
import threading
import time
import subprocess
from motor_daemon import MotorDaemon, CMD_STOP, CMD_UP, CMD_DOWN

# ── UI Constants ──────────────────────────────────────────────
WINDOW_TITLE   = "Motor Control"
REFRESH_MS     = 200          # UI refresh interval (5Hz is enough)
BG_COLOR       = "#1a1a2e"    # Dark navy background
BTN_UP_COLOR   = "#16213e"
BTN_DOWN_COLOR = "#16213e"
BTN_ACTIVE     = "#0f3460"
BTN_PRESS      = "#e94560"
CONSOLE_BG     = "#0f3460"
TEXT_COLOR     = "#e0e0e0"
GREEN          = "#00ff88"
RED            = "#ff4444"
YELLOW         = "#ffcc00"
FONT_BTN       = ("Helvetica", 22, "bold")
FONT_LABEL     = ("Helvetica", 11, "bold")
FONT_CONSOLE   = ("Courier",   11)
FONT_STATUS    = ("Courier",   13, "bold")


def get_pi_temp() -> str:
    """Read CPU temperature from system."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = int(f.read()) / 1000
        return f"{temp:.1f}°C"
    except Exception:
        return "N/A"


def get_pi_uptime() -> str:
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        h, m = divmod(int(secs), 3600)
        m, s = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        return "N/A"


def state_label(state: int) -> str:
    return {0: "STOP", 1: "UP  ", 2: "DOWN"}.get(state, "????")


class MotorControlApp:
    def __init__(self, root: tk.Tk, daemon: MotorDaemon):
        self.root   = root
        self.daemon = daemon

        # Track which buttons are currently held
        self._held = {"a": CMD_STOP, "b": CMD_STOP}
        self._held_lock = threading.Lock()

        self._build_ui()
        self._schedule_refresh()

    # ── UI Construction ───────────────────────────────────────

    def _build_ui(self):
        self.root.title(WINDOW_TITLE)
        self.root.configure(bg=BG_COLOR)
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))

        # ── Title bar ──
        title = tk.Label(self.root, text="⚙  MOTOR CONTROL SYSTEM",
                         font=("Helvetica", 16, "bold"),
                         bg=BG_COLOR, fg=TEXT_COLOR)
        title.pack(pady=(10, 0))

        # ── Main grid: [Motor A col] [Console] [Motor B col] ──
        main = tk.Frame(self.root, bg=BG_COLOR)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=2)
        main.columnconfigure(2, weight=1)
        main.rowconfigure(0, weight=1)

        self._build_motor_col(main, "MOTOR A", "a", col=0)
        self._build_console(main, col=1)
        self._build_motor_col(main, "MOTOR B", "b", col=2)

    def _build_motor_col(self, parent, label, motor_id, col):
        frame = tk.Frame(parent, bg=BG_COLOR)
        frame.grid(row=0, column=col, sticky="nsew", padx=8)
        frame.rowconfigure(0, weight=0)
        frame.rowconfigure(1, weight=1)
        frame.rowconfigure(2, weight=0)
        frame.rowconfigure(3, weight=1)
        frame.columnconfigure(0, weight=1)

        # Label
        tk.Label(frame, text=label, font=FONT_LABEL,
                 bg=BG_COLOR, fg=YELLOW).grid(row=0, column=0, pady=(0, 6))

        # UP button
        btn_up = tk.Button(frame, text="▲\nUP",
                           font=FONT_BTN, bg=BTN_UP_COLOR, fg=GREEN,
                           activebackground=BTN_PRESS, relief="raised",
                           borderwidth=3, cursor="hand2")
        btn_up.grid(row=1, column=0, sticky="nsew", pady=4)
        btn_up.bind("<ButtonPress-1>",   lambda e, m=motor_id: self._press(m, CMD_UP))
        btn_up.bind("<ButtonRelease-1>", lambda e, m=motor_id: self._release(m))

        # State indicator label
        state_var = tk.StringVar(value="STOP")
        state_lbl = tk.Label(frame, textvariable=state_var,
                             font=("Courier", 13, "bold"),
                             bg=BG_COLOR, fg=TEXT_COLOR)
        state_lbl.grid(row=2, column=0, pady=4)

        # DOWN button
        btn_down = tk.Button(frame, text="▼\nDOWN",
                             font=FONT_BTN, bg=BTN_DOWN_COLOR, fg=RED,
                             activebackground=BTN_PRESS, relief="raised",
                             borderwidth=3, cursor="hand2")
        btn_down.grid(row=3, column=0, sticky="nsew", pady=4)
        btn_down.bind("<ButtonPress-1>",   lambda e, m=motor_id: self._press(m, CMD_DOWN))
        btn_down.bind("<ButtonRelease-1>", lambda e, m=motor_id: self._release(m))

        # Store references for status updates
        if motor_id == "a":
            self._state_var_a = state_var
        else:
            self._state_var_b = state_var

    def _build_console(self, parent, col):
        frame = tk.Frame(parent, bg=CONSOLE_BG, relief="sunken", borderwidth=2)
        frame.grid(row=0, column=col, sticky="nsew", padx=8)
        frame.columnconfigure(0, weight=1)

        tk.Label(frame, text="● SYSTEM CONSOLE",
                 font=("Helvetica", 13, "bold"),
                 bg=CONSOLE_BG, fg=YELLOW).pack(pady=(10, 6))

        # Connection status
        self._conn_var = tk.StringVar(value="CONNECTING...")
        self._conn_lbl = tk.Label(frame, textvariable=self._conn_var,
                                  font=FONT_STATUS, bg=CONSOLE_BG, fg=YELLOW)
        self._conn_lbl.pack(pady=2)

        tk.Frame(frame, bg="#334466", height=1).pack(fill=tk.X, padx=10, pady=6)

        # Info lines
        self._info_vars = {}
        rows = [
            ("eth_status",  "ETH Link",    "—"),
            ("latency",     "Latency",     "—"),
            ("pkt_sent",    "Pkts Sent",   "0"),
            ("pkt_recv",    "Pkts Recv",   "0"),
            ("pkt_lost",    "Pkts Lost",   "0"),
            ("motor_a",     "Motor A",     "STOP"),
            ("motor_b",     "Motor B",     "STOP"),
            ("pi_temp",     "Pi Temp",     "—"),
            ("uptime",      "Uptime",      "—"),
        ]
        for key, label, default in rows:
            row_frame = tk.Frame(frame, bg=CONSOLE_BG)
            row_frame.pack(fill=tk.X, padx=14, pady=2)
            tk.Label(row_frame, text=f"{label}:", width=12, anchor="w",
                     font=FONT_CONSOLE, bg=CONSOLE_BG, fg="#aaaacc").pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            lbl = tk.Label(row_frame, textvariable=var, anchor="w",
                           font=FONT_CONSOLE, bg=CONSOLE_BG, fg=TEXT_COLOR)
            lbl.pack(side=tk.LEFT)
            self._info_vars[key] = (var, lbl)

        tk.Frame(frame, bg="#334466", height=1).pack(fill=tk.X, padx=10, pady=8)

        # Quit button
        tk.Button(frame, text="QUIT", font=("Helvetica", 11),
                  bg="#330000", fg=RED, command=self.root.destroy,
                  cursor="hand2").pack(pady=(0, 10))

    # ── Button handlers ───────────────────────────────────────

    def _press(self, motor_id: str, direction: int):
        with self._held_lock:
            self._held[motor_id] = direction
            self._send_current_state()

    def _release(self, motor_id: str):
        with self._held_lock:
            self._held[motor_id] = CMD_STOP
            self._send_current_state()

    def _send_current_state(self):
        """Must be called with _held_lock held."""
        self.daemon.send_command(self._held["a"], self._held["b"])

    # ── UI Refresh ────────────────────────────────────────────

    def _schedule_refresh(self):
        self._refresh()
        self.root.after(REFRESH_MS, self._schedule_refresh)

    def _refresh(self):
        s = self.daemon.get_status()

        # Connection banner
        if s["connected"]:
            self._conn_var.set("● CONNECTED")
            self._conn_lbl.configure(fg=GREEN)
        else:
            self._conn_var.set("✖ COMMS LOST")
            self._conn_lbl.configure(fg=RED)

        # Motor state labels (from Arduino confirmed state)
        self._state_var_a.set(state_label(s["motor_a"]))
        self._state_var_b.set(state_label(s["motor_b"]))

        # Console info lines
        def set_info(key, value, color=TEXT_COLOR):
            var, lbl = self._info_vars[key]
            var.set(value)
            lbl.configure(fg=color)

        eth_color = GREEN if s["connected"] else RED
        set_info("eth_status", "OK" if s["connected"] else "LOST", eth_color)
        set_info("latency",    f"{s['latency_ms']} ms")
        set_info("pkt_sent",   str(s["packets_sent"]))
        set_info("pkt_recv",   str(s["packets_recv"]))

        lost = s["packets_sent"] - s["packets_recv"]
        lost_color = RED if lost > 10 else TEXT_COLOR
        set_info("pkt_lost",   str(max(0, lost)), lost_color)

        set_info("motor_a",    state_label(s["motor_a"]))
        set_info("motor_b",    state_label(s["motor_b"]))
        set_info("pi_temp",    get_pi_temp())
        set_info("uptime",     get_pi_uptime())


# ── Entry point ───────────────────────────────────────────────

def main():
    daemon = MotorDaemon()
    daemon.start()

    root = tk.Tk()
    app  = MotorControlApp(root, daemon)

    try:
        root.mainloop()
    finally:
        daemon.stop()


if __name__ == "__main__":
    main()
