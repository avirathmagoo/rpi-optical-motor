#!/usr/bin/env python3
"""
motor_daemon.py — UDP communication daemon for motor control
Runs as a background thread, sends heartbeats, forwards commands,
receives status from Arduino.
"""

import socket
import threading
import time
import struct

# ── Network config ────────────────────────────────────────────
ARDUINO_IP   = "192.168.10.2"
ARDUINO_PORT = 5000
LOCAL_IP     = "192.168.10.1"
LOCAL_PORT   = 5001

# ── Packet constants ──────────────────────────────────────────
MAGIC_CMD  = 0xAB
MAGIC_ACK  = 0xBA
TYPE_CMD   = 0x01
TYPE_HB    = 0x02
CMD_STOP   = 0x00
CMD_UP     = 0x01
CMD_DOWN   = 0x02

# ── Timing ────────────────────────────────────────────────────
HEARTBEAT_INTERVAL = 0.1   # seconds — send heartbeat every 100ms
ACK_TIMEOUT        = 0.5   # seconds — comms lost if no ACK for 500ms


def xor_checksum(data: bytes) -> int:
    cs = 0
    for b in data:
        cs ^= b
    return cs


def build_packet(pkt_type: int, motor_a: int, motor_b: int, seq: int) -> bytes:
    body = bytes([MAGIC_CMD, seq & 0xFF, pkt_type, motor_a, motor_b])
    cs   = xor_checksum(body)
    return body + bytes([cs])


class MotorDaemon:
    """
    Runs two threads:
      - sender_thread : sends heartbeats at 10Hz; sends CMD when queued
      - receiver_thread: listens for ACK packets from Arduino
    Exposes simple API to the UI:
      send_command(motor_a, motor_b)
      get_status() -> dict
    """

    def __init__(self):
        self._seq          = 0
        self._lock         = threading.Lock()
        self._running      = False

        # Pending command (set by UI, consumed by sender thread)
        self._pending_cmd  = None   # (motor_a, motor_b) or None

        # Status exposed to UI
        self.status = {
            "connected"     : False,
            "last_ack_time" : 0.0,
            "latency_ms"    : 0.0,
            "motor_a"       : CMD_STOP,
            "motor_b"       : CMD_STOP,
            "packets_sent"  : 0,
            "packets_recv"  : 0,
            "packets_lost"  : 0,
        }

        # Track sent-time per seq for latency calculation
        self._sent_times   = {}

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((LOCAL_IP, LOCAL_PORT))
        self._sock.settimeout(0.05)   # 50ms receive timeout

    # ── Public API ────────────────────────────────────────────

    def send_command(self, motor_a: int, motor_b: int):
        """Called by UI when a button is pressed/released."""
        with self._lock:
            self._pending_cmd = (motor_a, motor_b)

    def get_status(self) -> dict:
        with self._lock:
            return dict(self.status)

    def start(self):
        self._running = True
        threading.Thread(target=self._sender_thread,   daemon=True).start()
        threading.Thread(target=self._receiver_thread, daemon=True).start()

    def stop(self):
        self._running = False
        self._sock.close()

    # ── Internal threads ──────────────────────────────────────

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

    def _sender_thread(self):
        while self._running:
            loop_start = time.time()

            with self._lock:
                pending = self._pending_cmd
                self._pending_cmd = None   # consume it

            seq = self._next_seq()

            if pending is not None:
                motor_a, motor_b = pending
                pkt = build_packet(TYPE_CMD, motor_a, motor_b, seq)
            else:
                pkt = build_packet(TYPE_HB, CMD_STOP, CMD_STOP, seq)

            try:
                self._sock.sendto(pkt, (ARDUINO_IP, ARDUINO_PORT))
                with self._lock:
                    self._sent_times[seq] = time.time()
                    self.status["packets_sent"] += 1
            except OSError:
                pass

            # Update connected flag based on last ACK time
            with self._lock:
                age = time.time() - self.status["last_ack_time"]
                self.status["connected"] = age < ACK_TIMEOUT

            # Sleep remainder of 100ms interval
            elapsed = time.time() - loop_start
            sleep_for = HEARTBEAT_INTERVAL - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    def _receiver_thread(self):
        while self._running:
            try:
                data, _ = self._sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                break

            if len(data) < 5:
                continue
            if data[0] != MAGIC_ACK:
                continue

            # Validate checksum
            if xor_checksum(data[:4]) != data[4]:
                continue

            seq_echo = data[1]
            motor_a  = data[2]
            motor_b  = data[3]

            now = time.time()
            with self._lock:
                self.status["last_ack_time"] = now
                self.status["packets_recv"] += 1
                self.status["motor_a"]  = motor_a
                self.status["motor_b"]  = motor_b
                self.status["connected"] = True

                # Calculate latency
                sent_t = self._sent_times.pop(seq_echo, None)
                if sent_t:
                    self.status["latency_ms"] = round((now - sent_t) * 1000, 1)
                    # Clean up old entries (older than 2 seconds)
                    cutoff = now - 2.0
                    self._sent_times = {
                        k: v for k, v in self._sent_times.items()
                        if v > cutoff
                    }
