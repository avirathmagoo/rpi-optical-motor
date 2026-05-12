#!/usr/bin/env python3
"""
ui.py  —  v3.2
Changes from v3.0:
  - Title changed to OPTICAL FIBER MOTOR CONTROL AND TRIGGER
  - FIRE button added to top of console panel (red, one-shot)
  - Removed LAST PKT AGE from console rows
  - QUIT and SHUTDOWN remain at bottom of console
"""

import tkinter as tk
import tkinter.messagebox as msgbox
import time
import subprocess
import os
from motor_daemon import MotorDaemon, CMD_STOP, CMD_UP, CMD_DOWN

# ── Refresh rate ──────────────────────────────────────────────
REFRESH_MS = 250

# ── Color palette — industrial dark theme ─────────────────────
C_BG         = "#0d0d0d"
C_PANEL      = "#141414"
C_BORDER     = "#2a2a2a"
C_ACCENT     = "#c8a800"
C_TEXT       = "#d0d0d0"
C_DIM        = "#555555"
C_GREEN      = "#00c853"
C_RED        = "#d50000"
C_YELLOW     = "#ffd600"
C_BTN_NORMAL = "#1a1a1a"
C_BTN_PRESS  = "#c8a800"
C_BTN_BORDER = "#333333"
C_UP_TEXT    = "#00c853"
C_DOWN_TEXT  = "#d50000"
C_SHUTDOWN   = "#8b0000"
C_FIRE_IDLE  = "#004d1a"    # Dark green when idle / ACK received
C_FIRE_WAIT  = "#5a0000"    # Dark red while waiting for ACK
C_FIRE_PRESS = "#d50000"    # Bright red on press

# ── Fonts ─────────────────────────────────────────────────────
F_TITLE   = ("Courier", 13, "bold")
F_BTN     = ("Courier", 20, "bold")
F_FIRE    = ("Courier", 18, "bold")
F_LABEL   = ("Courier", 11, "bold")
F_CONSOLE = ("Courier", 10)
F_VALUE   = ("Courier", 12, "bold")
F_STATUS  = ("Courier", 12, "bold")
F_MOTOR   = ("Courier", 16, "bold")
F_SECTION = ("Courier", 11, "bold")


# ── System helpers ────────────────────────────────────────────

def get_pi_temp() -> str:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return f"{int(f.read()) / 1000:.1f}°C"
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


def get_cpu_percent() -> str:
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        fields = [float(x) for x in line.strip().split()[1:]]
        idle  = fields[3]
        total = sum(fields)
        if not hasattr(get_cpu_percent, "_prev"):
            get_cpu_percent._prev = (idle, total)
            return "—"
        prev_idle, prev_total = get_cpu_percent._prev
        get_cpu_percent._prev = (idle, total)
        d_idle  = idle  - prev_idle
        d_total = total - prev_total
        if d_total == 0:
            return "—"
        return f"{100.0 * (1.0 - d_idle / d_total):.1f}%"
    except Exception:
        return "N/A"


def state_label(state: int) -> str:
    return {CMD_STOP: "STOP", CMD_UP: "UP  ", CMD_DOWN: "DOWN"}.get(state, "????")


def fmt_session(secs: float) -> str:
    if secs <= 0:
        return "—"
    h, m = divmod(int(secs), 3600)
    m, s = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


# ── Main application ──────────────────────────────────────────

class MotorControlApp:
    def __init__(self, root: tk.Tk, daemon: MotorDaemon):
        self.root   = root
        self.daemon = daemon
        self._ui_held = {"a": CMD_STOP, "b": CMD_STOP}
        self._fire_flash_id = None   # pending after() id for flash reset
        self._build_ui()
        self._schedule_refresh()

    # ── UI Construction ───────────────────────────────────────

    def _build_ui(self):
        self.root.title("OPTICAL FIBER MOTOR CONTROL AND TRIGGER  v3.2")
        self.root.configure(bg=C_BG)
        self.root.attributes("-fullscreen", True)

        self.root.bind("<Escape>",
                       lambda e: self.root.attributes("-fullscreen", False))

        self._build_statusbar()

        main = tk.Frame(self.root, bg=C_BG)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=4)
        main.columnconfigure(2, weight=3)
        main.rowconfigure(0, weight=1)

        self._build_motor_panel(main, "MOTOR  A", "a", col=0)
        self._build_console(main, col=1)
        self._build_motor_panel(main, "MOTOR  B", "b", col=2)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=C_PANEL, height=42,
                       highlightbackground=C_ACCENT,
                       highlightthickness=1)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        tk.Label(bar,
                 text="▶  OPTICAL FIBER MOTOR CONTROL AND TRIGGER  v3.2",
                 font=F_TITLE, bg=C_PANEL, fg=C_ACCENT).pack(
                     side=tk.LEFT, padx=16)

        self._lat_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self._lat_var,
                 font=F_CONSOLE, bg=C_PANEL, fg=C_DIM).pack(
                     side=tk.RIGHT, padx=6)

        self._conn_var = tk.StringVar(value="● INITIALISING")
        self._conn_lbl = tk.Label(bar, textvariable=self._conn_var,
                                  font=F_STATUS, bg=C_PANEL, fg=C_YELLOW)
        self._conn_lbl.pack(side=tk.RIGHT, padx=16)

    def _build_motor_panel(self, parent, label, motor_id, col):
        outer = tk.Frame(parent, bg=C_BORDER, padx=1, pady=1)
        outer.grid(row=0, column=col, sticky="nsew", padx=6, pady=2)

        frame = tk.Frame(outer, bg=C_PANEL)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.rowconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)
        frame.columnconfigure(0, weight=1)

        tk.Label(frame, text=f"━━  {label}  ━━",
                 font=F_SECTION, bg=C_PANEL, fg=C_ACCENT).grid(
                     row=0, column=0, pady=(10, 4))

        btn_up = tk.Button(
            frame, text="▲\nUP",
            font=F_BTN, bg=C_BTN_NORMAL, fg=C_UP_TEXT,
            activebackground=C_BTN_NORMAL,
            activeforeground=C_UP_TEXT,
            relief="flat", borderwidth=0,
            highlightbackground=C_BTN_BORDER,
            highlightthickness=2,
            cursor="hand2"
        )
        btn_up.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        btn_up.bind("<ButtonPress-1>",
                    lambda e, m=motor_id: self._press(m, CMD_UP, btn_up, "up"))
        btn_up.bind("<ButtonRelease-1>",
                    lambda e, m=motor_id: self._release(m, btn_up, "up"))

        state_var = tk.StringVar(value="STOP")
        state_lbl = tk.Label(frame, textvariable=state_var,
                             font=F_MOTOR, bg=C_PANEL, fg=C_DIM, width=6)
        state_lbl.grid(row=2, column=0, pady=6)

        btn_down = tk.Button(
            frame, text="▼\nDOWN",
            font=F_BTN, bg=C_BTN_NORMAL, fg=C_DOWN_TEXT,
            activebackground=C_BTN_NORMAL,
            activeforeground=C_DOWN_TEXT,
            relief="flat", borderwidth=0,
            highlightbackground=C_BTN_BORDER,
            highlightthickness=2,
            cursor="hand2"
        )
        btn_down.grid(row=3, column=0, sticky="nsew", padx=12, pady=4)
        btn_down.bind("<ButtonPress-1>",
                      lambda e, m=motor_id: self._press(m, CMD_DOWN, btn_down, "down"))
        btn_down.bind("<ButtonRelease-1>",
                      lambda e, m=motor_id: self._release(m, btn_down, "down"))

        tk.Frame(frame, bg=C_PANEL, height=8).grid(row=4, column=0)

        if motor_id == "a":
            self._state_var_a = state_var
            self._state_lbl_a = state_lbl
        else:
            self._state_var_b = state_var
            self._state_lbl_b = state_lbl

    def _build_console(self, parent, col):
        outer = tk.Frame(parent, bg=C_ACCENT, padx=1, pady=1)
        outer.grid(row=0, column=col, sticky="nsew", padx=6, pady=2)

        frame = tk.Frame(outer, bg=C_PANEL)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        # ── Section heading ──
        tk.Label(frame, text="━━  SYSTEM CONSOLE  ━━",
                 font=F_SECTION, bg=C_PANEL, fg=C_ACCENT).grid(
                     row=0, column=0, columnspan=2, pady=(10, 4))

        # ── FIRE button — top of console, full width, prominent ──
        fire_outer = tk.Frame(frame, bg="#3a0000", padx=2, pady=2)
        fire_outer.grid(row=1, column=0, columnspan=2,
                        sticky="ew", padx=14, pady=(2, 8))
        fire_outer.columnconfigure(0, weight=1)

        self._fire_btn = tk.Button(
            fire_outer,
            text="🟢  FIRE",
            font=F_FIRE,
            bg=C_FIRE_IDLE,
            fg="#00c853",
            activebackground=C_FIRE_IDLE,
            activeforeground="#00c853",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            pady=10,
        )
        self._fire_btn.grid(row=0, column=0, sticky="ew")
        self._fire_btn.bind("<ButtonPress-1>",   self._fire_press)
        self._fire_btn.bind("<ButtonRelease-1>", self._fire_release)

        # ── Console data rows ──
        self._crows = {}
        rows = [
            ("_hdr_comms",  "── COMMUNICATIONS ──", None),
            ("eth_status",  "ETH LINK",             "—"),
            ("latency",     "LATENCY",               "—"),
            ("hb_rate",     "HB RATE",               "—"),
            ("lost",        "PKTS LOST",             "0"),
            ("_hdr_motors", "── MOTORS ──",          None),
            ("motor_a",     "MOTOR A",               "STOP"),
            ("motor_b",     "MOTOR B",               "STOP"),
            ("cmd_source",  "CMD SOURCE",            "—"),
            ("_hdr_sys",    "── SYSTEM ──",          None),
            ("pi_temp",     "PI TEMP",               "—"),
            ("cpu",         "CPU LOAD",              "—"),
            ("session",     "SESSION TIME",          "—"),
            ("uptime",      "UPTIME",                "—"),
            ("gpio",        "HW BUTTONS",            "—"),
            ("socket",      "SOCKET",                "—"),
        ]

        for i, row in enumerate(rows, start=2):   # start=2: row 0=title, row 1=fire btn
            key, label, default = row
            if key.startswith("_hdr"):
                tk.Label(frame, text=label,
                         font=("Courier", 9, "bold"),
                         bg=C_PANEL, fg=C_DIM).grid(
                             row=i, column=0, columnspan=2,
                             sticky="w", padx=14, pady=(6, 1))
                continue

            tk.Label(frame, text=label + ":", font=F_LABEL,
                     bg=C_PANEL, fg=C_DIM, anchor="w").grid(
                         row=i, column=0, sticky="w", padx=14, pady=1)

            var = tk.StringVar(value=default or "—")
            lbl = tk.Label(frame, textvariable=var, font=F_VALUE,
                           bg=C_PANEL, fg=C_TEXT, anchor="w")
            lbl.grid(row=i, column=1, sticky="w", padx=4, pady=1)
            self._crows[key] = (var, lbl)

        # ── Divider ──
        sep_row = len(rows) + 3
        tk.Frame(frame, bg=C_BORDER, height=1).grid(
            row=sep_row, column=0, columnspan=2,
            sticky="ew", padx=10, pady=8)

        # ── Quit button (development use) ──
        tk.Button(
            frame, text="[ QUIT ]",
            font=("Courier", 10, "bold"),
            bg=C_PANEL, fg=C_DIM,
            activebackground=C_PANEL,
            relief="flat",
            command=self._quit,
            cursor="hand2"
        ).grid(row=sep_row + 1, column=0, columnspan=2, pady=(0, 4))

        # ── Shutdown button ──
        tk.Button(
            frame, text="⏻  SHUTDOWN PI",
            font=("Courier", 11, "bold"),
            bg=C_SHUTDOWN, fg=C_TEXT,
            activebackground=C_SHUTDOWN,
            activeforeground=C_TEXT,
            relief="flat",
            command=self._confirm_shutdown,
            cursor="hand2"
        ).grid(row=sep_row + 2, column=0, columnspan=2,
               sticky="ew", padx=14, pady=(0, 12))

    # ── Button handlers ───────────────────────────────────────

    def _press(self, motor_id: str, direction: int,
               btn: tk.Button, side: str):
        btn.configure(bg=C_BTN_PRESS, fg=C_BG)
        self._ui_held[motor_id] = direction
        self.daemon.send_command(
            self._ui_held["a"], self._ui_held["b"], source="UI")

    def _release(self, motor_id: str, btn: tk.Button, side: str):
        fg = C_UP_TEXT if side == "up" else C_DOWN_TEXT
        btn.configure(bg=C_BTN_NORMAL, fg=fg)
        self._ui_held[motor_id] = CMD_STOP
        self.daemon.send_command(
            self._ui_held["a"], self._ui_held["b"], source="UI")

    def _fire_press(self, event=None):
        """Send a single FIRE pulse and turn button red until ACK received."""
        self.daemon.send_fire(source="UI")
        self._fire_btn.configure(bg=C_FIRE_PRESS, fg="#ffffff", text="🔴  FIRE")
        # Cancel any pending reset (safety — shouldn't normally be pending)
        if self._fire_flash_id is not None:
            self.root.after_cancel(self._fire_flash_id)
            self._fire_flash_id = None

    def _fire_release(self, event=None):
        pass   # No action on release — fire is one-shot on press only

    def _fire_reset(self):
        """Called by _refresh when ACK arrives — restore green."""
        self._fire_btn.configure(bg=C_FIRE_IDLE, fg="#00c853", text="🟢  FIRE")
        self._fire_flash_id = None

    def _quit(self):
        self.daemon.stop()
        self.root.destroy()

    def _confirm_shutdown(self):
        confirmed = msgbox.askyesno(
            title="Shutdown",
            message="Shutdown the Raspberry Pi?\n\nAll motor commands will stop.",
            icon=msgbox.WARNING,
            default=msgbox.NO
        )
        if confirmed:
            self.daemon.stop()
            subprocess.run(["sudo", "shutdown", "-h", "now"])

    # ── Refresh ───────────────────────────────────────────────

    def _set_row(self, key: str, value: str, color: str = C_TEXT):
        if key in self._crows:
            var, lbl = self._crows[key]
            var.set(value)
            lbl.configure(fg=color)

    def _schedule_refresh(self):
        self._refresh()
        self.root.after(REFRESH_MS, self._schedule_refresh)

    def _refresh(self):
        s = self.daemon.get_status()

        # ── Status bar ──
        if not s["socket_ready"]:
            self._conn_var.set("◌  WAITING FOR NETWORK")
            self._conn_lbl.configure(fg=C_DIM)
            self._lat_var.set("")
        elif s["connected"]:
            self._conn_var.set("●  LINK  OK")
            self._conn_lbl.configure(fg=C_GREEN)
            self._lat_var.set(f"RTT {s['latency_ms']} ms")
        else:
            self._conn_var.set("✖  COMMS  LOST")
            self._conn_lbl.configure(fg=C_RED)
            self._lat_var.set("")

        # ── Motor state labels ──
        def motor_color(state):
            if state == CMD_UP:   return C_GREEN
            if state == CMD_DOWN: return C_RED
            return C_DIM

        self._state_var_a.set(state_label(s["motor_a"]))
        self._state_lbl_a.configure(fg=motor_color(s["motor_a"]))
        self._state_var_b.set(state_label(s["motor_b"]))
        self._state_lbl_b.configure(fg=motor_color(s["motor_b"]))

        # ── Console rows ──
        if not s["socket_ready"]:
            self._set_row("eth_status", "NO SOCKET", C_DIM)
        else:
            eth_c = C_GREEN if s["connected"] else C_RED
            self._set_row("eth_status",
                          "OK" if s["connected"] else "LOST", eth_c)

        self._set_row("latency",
                      f"{s['latency_ms']} ms" if s["connected"] else "—",
                      C_GREEN if s["latency_ms"] < 50 else C_YELLOW)

        self._set_row("hb_rate",
                      f"{s['hb_rate_hz']} Hz" if s["socket_ready"] else "—")

        lost = s["lost_since_conn"]
        self._set_row("lost", str(lost),
                      C_RED if lost > 5 else C_TEXT)

        self._set_row("motor_a",
                      state_label(s["motor_a"]), motor_color(s["motor_a"]))
        self._set_row("motor_b",
                      state_label(s["motor_b"]), motor_color(s["motor_b"]))
        self._set_row("cmd_source", s["cmd_source"],
                      C_ACCENT if s["cmd_source"] == "HW Button" else C_TEXT)

        temp_str = get_pi_temp()
        try:
            temp_val = float(temp_str.replace("°C", ""))
            temp_c = (C_RED    if temp_val > 75 else
                      C_YELLOW if temp_val > 60 else C_GREEN)
        except Exception:
            temp_c = C_TEXT
        self._set_row("pi_temp", temp_str, temp_c)

        self._set_row("cpu",     get_cpu_percent())
        self._set_row("session", fmt_session(s["session_time_s"]))
        self._set_row("uptime",  get_pi_uptime())

        gpio_str = "ENABLED" if s["gpio_enabled"] else "DISABLED"
        self._set_row("gpio", gpio_str,
                      C_GREEN if s["gpio_enabled"] else C_DIM)

        sock_str = "READY" if s["socket_ready"] else "WAITING..."
        self._set_row("socket", sock_str,
                      C_GREEN if s["socket_ready"] else C_YELLOW)

        # ── FIRE button state — green=idle, red=waiting for ACK ──
        if not s["fire_ack_pending"] and self._fire_flash_id is None:
            current_bg = self._fire_btn.cget("bg")
            if current_bg != C_FIRE_IDLE:
                self._fire_reset()


# ── Entry point ───────────────────────────────────────────────

def main():
    daemon = MotorDaemon()
    daemon.start()

    root = tk.Tk()
    MotorControlApp(root, daemon)

    try:
        root.mainloop()
    finally:
        daemon.stop()


if __name__ == "__main__":
    main()