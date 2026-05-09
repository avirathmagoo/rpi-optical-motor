#!/usr/bin/env python3
"""
motor_daemon.py  —  v3.0
UDP communication + GPIO hardware buttons and LEDs for Raspberry Pi.

Key reliability feature: socket creation is retried in the background.
The UI opens instantly even if Ethernet is not connected yet.
When the cable is plugged in, the daemon connects automatically.

Threads:
  sender_thread   — sends heartbeat every 250ms or CMD when queued
  receiver_thread — listens for ACK from Arduino, updates status
  gpio_thread     — polls hardware buttons at 50ms, drives LEDs
"""

import socket
import threading
import time

# ── GPIO setup (graceful fallback) ───────────────────────────
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[daemon] RPi.GPIO not available — hardware buttons/LEDs disabled")

# ── Network config ────────────────────────────────────────────
ARDUINO_IP   = "192.168.10.2"
ARDUINO_PORT = 5000
LOCAL_IP     = "192.168.10.1"
LOCAL_PORT   = 5001

# ── Packet constants ──────────────────────────────────────────
MAGIC_CMD = 0xAB
MAGIC_ACK = 0xBA
TYPE_CMD  = 0x01
TYPE_HB   = 0x02
CMD_STOP  = 0x00
CMD_UP    = 0x01
CMD_DOWN  = 0x02

# ── Timing ───────────────────────────────────────────────────
HB_INTERVAL   = 0.25   # Heartbeat every 250ms (4Hz)
COMMS_TIMEOUT = 0.6    # Lost if no ACK for 600ms
HB_LED_ON_S   = 0.06   # Heartbeat LED blink duration
SOCKET_RETRY  = 2.0    # Retry socket bind every 2s if not ready

# ── GPIO Pin assignments ──────────────────────────────────────
PIN_BTN_A_UP   = 17
PIN_BTN_A_DOWN = 27
PIN_BTN_B_UP   = 22
PIN_BTN_B_DOWN = 23
PIN_LED_GREEN  = 24    # Connected
PIN_LED_RED    = 25    # Disconnected
PIN_LED_HB     = 12    # Heartbeat


def xor_checksum(data: bytes) -> int:
    cs = 0
    for b in data:
        cs ^= b
    return cs


def build_packet(pkt_type: int, motor_a: int, motor_b: int, seq: int) -> bytes:
    body = bytes([MAGIC_CMD, seq & 0xFF, pkt_type, motor_a, motor_b])
    return body + bytes([xor_checksum(body)])


class MotorDaemon:
    def __init__(self):
        self._seq       = 0
        self._lock      = threading.Lock()
        self._running   = False

        # Pending command set by UI or GPIO buttons
        self._pending_cmd = None

        # Hardware button states
        self._hw_held = {"a": CMD_STOP, "b": CMD_STOP}

        # Socket — created lazily when network is available
        self._sock        = None
        self._sock_ready  = False

        # Status dict — all UI reads go through get_status()
        self._status = {
            "connected"      : False,
            "last_ack_time"  : 0.0,
            "latency_ms"     : 0.0,
            "motor_a"        : CMD_STOP,
            "motor_b"        : CMD_STOP,
            "packets_sent"   : 0,
            "packets_recv"   : 0,
            "lost_since_conn": 0,
            "hb_rate_hz"     : 0.0,
            "last_pkt_age_ms": 0.0,
            "session_time_s" : 0.0,
            "session_start"  : 0.0,
            "cmd_source"     : "—",
            "gpio_enabled"   : GPIO_AVAILABLE,
            "socket_ready"   : False,
        }

        # Internal tracking
        self._sent_times    = {}
        self._hb_send_times = []
        self._was_connected = False
        self._baseline_sent = 0
        self._baseline_recv = 0

        # GPIO init
        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in [PIN_BTN_A_UP, PIN_BTN_A_DOWN,
                        PIN_BTN_B_UP, PIN_BTN_B_DOWN]:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            for pin in [PIN_LED_GREEN, PIN_LED_RED, PIN_LED_HB]:
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
            GPIO.output(PIN_LED_RED, GPIO.HIGH)   # Start red

    # ── Socket management ─────────────────────────────────────

    def _try_bind_socket(self) -> bool:
        """Try to create and bind the UDP socket. Returns True on success."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind((LOCAL_IP, LOCAL_PORT))
            s.settimeout(0.05)
            self._sock       = s
            self._sock_ready = True
            with self._lock:
                self._status["socket_ready"] = True
            print(f"[daemon] Socket bound to {LOCAL_IP}:{LOCAL_PORT}")
            return True
        except OSError as e:
            print(f"[daemon] Socket not ready yet: {e} — retrying in {SOCKET_RETRY}s")
            return False

    # ── Public API ────────────────────────────────────────────

    def send_command(self, motor_a: int, motor_b: int, source: str = "UI"):
        with self._lock:
            self._pending_cmd = (motor_a, motor_b)
            self._status["cmd_source"] = source

    def get_status(self) -> dict:
        with self._lock:
            s = dict(self._status)
            s["last_pkt_age_ms"] = round(
                (time.time() - s["last_ack_time"]) * 1000, 1
            ) if s["last_ack_time"] > 0 else 0.0
            if s["session_start"] > 0:
                s["session_time_s"] = round(
                    time.time() - s["session_start"], 0)
            return s

    def start(self):
        self._running = True
        threading.Thread(target=self._sender_thread,
                         daemon=True, name="sender").start()
        threading.Thread(target=self._receiver_thread,
                         daemon=True, name="receiver").start()
        if GPIO_AVAILABLE:
            threading.Thread(target=self._gpio_thread,
                             daemon=True, name="gpio").start()

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if GPIO_AVAILABLE:
            for pin in [PIN_LED_GREEN, PIN_LED_RED, PIN_LED_HB]:
                GPIO.output(pin, GPIO.LOW)
            GPIO.cleanup()

    # ── Sender thread ─────────────────────────────────────────

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

    def _sender_thread(self):
        hb_led_off_time = 0.0

        while self._running:
            loop_start = time.time()

            # ── Ensure socket is ready ──
            if not self._sock_ready:
                self._try_bind_socket()
                time.sleep(SOCKET_RETRY)
                continue

            # ── Build and send packet ──
            with self._lock:
                pending = self._pending_cmd
                self._pending_cmd = None

            seq = self._next_seq()

            if pending is not None:
                motor_a, motor_b = pending
                pkt = build_packet(TYPE_CMD, motor_a, motor_b, seq)
            else:
                pkt = build_packet(TYPE_HB, CMD_STOP, CMD_STOP, seq)

            try:
                self._sock.sendto(pkt, (ARDUINO_IP, ARDUINO_PORT))
                now = time.time()
                with self._lock:
                    self._sent_times[seq] = now
                    self._status["packets_sent"] += 1
                    self._hb_send_times.append(now)
                    cutoff = now - 4.0
                    self._hb_send_times = [
                        t for t in self._hb_send_times if t > cutoff]
                    self._status["hb_rate_hz"] = round(
                        len(self._hb_send_times) / 4.0, 1)
            except OSError:
                # Socket died (cable pulled etc) — re-bind next cycle
                self._sock_ready = False
                with self._lock:
                    self._status["socket_ready"] = False
                time.sleep(SOCKET_RETRY)
                continue

            # ── Update connection state ──
            with self._lock:
                age       = time.time() - self._status["last_ack_time"]
                connected = (age < COMMS_TIMEOUT and
                             self._status["last_ack_time"] > 0)

                if connected and not self._was_connected:
                    # Just reconnected
                    self._baseline_sent = self._status["packets_sent"]
                    self._baseline_recv = self._status["packets_recv"]
                    self._status["session_start"]   = time.time()
                    self._status["lost_since_conn"] = 0

                self._was_connected     = connected
                self._status["connected"] = connected

                if connected:
                    sent = (self._status["packets_sent"] - self._baseline_sent)
                    recv = (self._status["packets_recv"] - self._baseline_recv)
                    self._status["lost_since_conn"] = max(0, sent - recv)

            # ── GPIO LEDs ──
            if GPIO_AVAILABLE:
                with self._lock:
                    conn = self._status["connected"]
                GPIO.output(PIN_LED_GREEN, GPIO.HIGH if conn else GPIO.LOW)
                GPIO.output(PIN_LED_RED,   GPIO.LOW  if conn else GPIO.HIGH)

                # HB LED off timer
                if time.time() > hb_led_off_time:
                    GPIO.output(PIN_LED_HB, GPIO.LOW)
                GPIO.output(PIN_LED_HB, GPIO.HIGH)
                hb_led_off_time = time.time() + HB_LED_ON_S

            elapsed   = time.time() - loop_start
            sleep_for = HB_INTERVAL - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    # ── Receiver thread ───────────────────────────────────────

    def _receiver_thread(self):
        while self._running:
            # Wait until socket is ready
            if not self._sock_ready or self._sock is None:
                time.sleep(0.5)
                continue

            try:
                data, _ = self._sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(0.5)
                continue

            if len(data) < 5:
                continue
            if data[0] != MAGIC_ACK:
                continue
            if xor_checksum(data[:4]) != data[4]:
                continue

            seq_echo = data[1]
            motor_a  = data[2]
            motor_b  = data[3]
            now      = time.time()

            with self._lock:
                self._status["last_ack_time"] = now
                self._status["packets_recv"] += 1
                self._status["motor_a"]       = motor_a
                self._status["motor_b"]       = motor_b

                sent_t = self._sent_times.pop(seq_echo, None)
                if sent_t:
                    self._status["latency_ms"] = round(
                        (now - sent_t) * 1000, 1)
                cutoff = now - 3.0
                self._sent_times = {
                    k: v for k, v in self._sent_times.items()
                    if v > cutoff}

    # ── GPIO thread ──────────────────────────────────────────

    def _gpio_thread(self):
        """Poll hardware buttons at 50ms. Buttons are active LOW."""
        while self._running:
            a_up   = not GPIO.input(PIN_BTN_A_UP)
            a_down = not GPIO.input(PIN_BTN_A_DOWN)
            b_up   = not GPIO.input(PIN_BTN_B_UP)
            b_down = not GPIO.input(PIN_BTN_B_DOWN)

            motor_a = CMD_UP   if a_up   else (CMD_DOWN if a_down else CMD_STOP)
            motor_b = CMD_UP   if b_up   else (CMD_DOWN if b_down else CMD_STOP)

            with self._lock:
                prev_a = self._hw_held["a"]
                prev_b = self._hw_held["b"]
                self._hw_held["a"] = motor_a
                self._hw_held["b"] = motor_b

            if motor_a != prev_a or motor_b != prev_b:
                self.send_command(motor_a, motor_b, source="HW Button")

            time.sleep(0.05)
