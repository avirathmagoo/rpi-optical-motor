#!/usr/bin/env python3
"""
motor_daemon.py  —  v3.1
Fixes:
  - GPIO crash on stop() when GPIO was not fully initialised
  - Ethernet cable reconnect: properly closes and rebinds socket
  - Cleaner thread lifecycle
"""

import socket
import threading
import time

# ── GPIO setup ────────────────────────────────────────────────
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
SOCKET_RETRY  = 2.0    # Retry socket bind every 2s

# ── GPIO Pin assignments (BCM) ────────────────────────────────
PIN_BTN_A_UP   = 17
PIN_BTN_A_DOWN = 27
PIN_BTN_B_UP   = 22
PIN_BTN_B_DOWN = 23
PIN_LED_GREEN  = 24
PIN_LED_RED    = 25
PIN_LED_HB     = 12

ALL_LED_PINS = [PIN_LED_GREEN, PIN_LED_RED, PIN_LED_HB]
ALL_BTN_PINS = [PIN_BTN_A_UP, PIN_BTN_A_DOWN, PIN_BTN_B_UP, PIN_BTN_B_DOWN]


def xor_checksum(data: bytes) -> int:
    cs = 0
    for b in data:
        cs ^= b
    return cs


def build_packet(pkt_type: int, motor_a: int, motor_b: int, seq: int) -> bytes:
    body = bytes([MAGIC_CMD, seq & 0xFF, pkt_type, motor_a, motor_b])
    return body + bytes([xor_checksum(body)])


def _safe_gpio_output(pin: int, value):
    """Write to GPIO pin, silently ignore if GPIO not ready."""
    try:
        GPIO.output(pin, value)
    except Exception:
        pass


class MotorDaemon:
    def __init__(self):
        self._seq            = 0
        self._lock           = threading.Lock()
        self._running        = False
        self._gpio_ready     = False   # True only after full GPIO init succeeds

        self._pending_cmd    = None
        self._hw_held        = {"a": CMD_STOP, "b": CMD_STOP}

        self._sock           = None
        self._sock_ready     = False

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

        self._sent_times     = {}
        self._hb_send_times  = []
        self._was_connected  = False
        self._baseline_sent  = 0
        self._baseline_recv  = 0

        self._gpio_init()

    # ── GPIO ──────────────────────────────────────────────────

    def _gpio_init(self):
        if not GPIO_AVAILABLE:
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in ALL_BTN_PINS:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            for pin in ALL_LED_PINS:
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
            GPIO.output(PIN_LED_RED, GPIO.HIGH)   # Start red
            self._gpio_ready = True
            print("[daemon] GPIO initialised OK")
        except Exception as e:
            print(f"[daemon] GPIO init failed: {e}")
            self._gpio_ready = False

    def _gpio_set_leds(self, green: bool, red: bool, hb: bool = False):
        if not self._gpio_ready:
            return
        _safe_gpio_output(PIN_LED_GREEN, GPIO.HIGH if green else GPIO.LOW)
        _safe_gpio_output(PIN_LED_RED,   GPIO.HIGH if red   else GPIO.LOW)
        _safe_gpio_output(PIN_LED_HB,    GPIO.HIGH if hb    else GPIO.LOW)

    # ── Socket management ─────────────────────────────────────

    def _close_socket(self):
        """Close current socket cleanly."""
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._sock_ready = False
        with self._lock:
            self._status["socket_ready"] = False

    def _try_bind_socket(self) -> bool:
        """Close old socket and try to create a fresh one. Returns True on success."""
        self._close_socket()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((LOCAL_IP, LOCAL_PORT))
            s.settimeout(0.05)
            self._sock       = s
            self._sock_ready = True
            with self._lock:
                self._status["socket_ready"] = True
            print(f"[daemon] Socket bound to {LOCAL_IP}:{LOCAL_PORT}")
            return True
        except OSError as e:
            print(f"[daemon] Socket bind failed: {e} — retry in {SOCKET_RETRY}s")
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
        if self._gpio_ready:
            threading.Thread(target=self._gpio_thread,
                             daemon=True, name="gpio").start()

    def stop(self):
        self._running = False
        self._close_socket()

        # Only touch GPIO if we successfully initialised it
        if self._gpio_ready:
            try:
                for pin in ALL_LED_PINS:
                    _safe_gpio_output(pin, GPIO.LOW)
                GPIO.cleanup()
            except Exception as e:
                print(f"[daemon] GPIO cleanup error (safe to ignore): {e}")
        self._gpio_ready = False

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
                self._gpio_set_leds(green=False, red=True)
                if not self._try_bind_socket():
                    time.sleep(SOCKET_RETRY)
                    continue

            # ── Build packet ──
            with self._lock:
                pending = self._pending_cmd
                self._pending_cmd = None

            seq = self._next_seq()
            if pending is not None:
                motor_a, motor_b = pending
                pkt = build_packet(TYPE_CMD, motor_a, motor_b, seq)
            else:
                pkt = build_packet(TYPE_HB, CMD_STOP, CMD_STOP, seq)

            # ── Send packet ──
            send_ok = False
            try:
                self._sock.sendto(pkt, (ARDUINO_IP, ARDUINO_PORT))
                send_ok = True
                now = time.time()
                with self._lock:
                    self._sent_times[seq] = now
                    self._status["packets_sent"] += 1
                    self._hb_send_times.append(now)
                    cutoff = now - 4.0
                    self._hb_send_times = [t for t in self._hb_send_times
                                           if t > cutoff]
                    self._status["hb_rate_hz"] = round(
                        len(self._hb_send_times) / 4.0, 1)
            except OSError as e:
                print(f"[daemon] Send error: {e} — rebinding socket")
                # Socket is dead — tear it down and reconnect next cycle
                self._close_socket()
                with self._lock:
                    self._status["connected"]   = False
                    self._was_connected         = False
                time.sleep(SOCKET_RETRY)
                continue

            # ── Update connection state ──
            with self._lock:
                age       = time.time() - self._status["last_ack_time"]
                connected = (age < COMMS_TIMEOUT and
                             self._status["last_ack_time"] > 0)

                if connected and not self._was_connected:
                    # Just reconnected — reset counters
                    self._baseline_sent             = self._status["packets_sent"]
                    self._baseline_recv             = self._status["packets_recv"]
                    self._status["session_start"]   = time.time()
                    self._status["lost_since_conn"] = 0
                    print("[daemon] Connection established")

                if not connected and self._was_connected:
                    print("[daemon] Connection lost")

                self._was_connected       = connected
                self._status["connected"] = connected

                if connected:
                    sent = self._status["packets_sent"] - self._baseline_sent
                    recv = self._status["packets_recv"] - self._baseline_recv
                    self._status["lost_since_conn"] = max(0, sent - recv)

            # ── GPIO LEDs ──
            if self._gpio_ready:
                with self._lock:
                    conn = self._status["connected"]
                self._gpio_set_leds(green=conn, red=not conn)

                # HB LED blink
                if time.time() > hb_led_off_time:
                    _safe_gpio_output(PIN_LED_HB, GPIO.LOW)
                if send_ok:
                    _safe_gpio_output(PIN_LED_HB, GPIO.HIGH)
                    hb_led_off_time = time.time() + HB_LED_ON_S

            elapsed   = time.time() - loop_start
            sleep_for = HB_INTERVAL - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    # ── Receiver thread ───────────────────────────────────────

    def _receiver_thread(self):
        while self._running:
            if not self._sock_ready or self._sock is None:
                time.sleep(0.2)
                continue

            try:
                data, _ = self._sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                # Socket died — sender thread will handle rebind
                time.sleep(0.2)
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
                self._sent_times = {k: v for k, v in self._sent_times.items()
                                    if v > cutoff}

    # ── GPIO thread ───────────────────────────────────────────

    def _gpio_thread(self):
        while self._running:
            try:
                a_up   = not GPIO.input(PIN_BTN_A_UP)
                a_down = not GPIO.input(PIN_BTN_A_DOWN)
                b_up   = not GPIO.input(PIN_BTN_B_UP)
                b_down = not GPIO.input(PIN_BTN_B_DOWN)
            except Exception:
                time.sleep(0.1)
                continue

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
