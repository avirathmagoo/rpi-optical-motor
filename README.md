# Motor Control System v3.0

A networked motor control system combining **Raspberry Pi 5** (touchscreen UI + daemon) and **Arduino Uno** (motor driver) communicating via direct Ethernet with automatic failsafe protection.

## System Overview

```
┌─────────────────────────────┐  Ethernet  ┌──────────────────────┐
│    Raspberry Pi 5           │◄──────────►│    Arduino Uno       │
│ • Touchscreen UI (ui.py)    │            │ + W5500 Module       │
│ • UDP Daemon (motor_daemon) │ CMD/HB/ACK │ • Motor Driver       │
│ IP: 192.168.10.1:5001      │            │ IP: 192.168.10.2:5000│
│ GPIO: buttons + LEDs        │            │ GPIO: motors + LEDs  │
└─────────────────────────────┘            └──────────────────────┘
```

## How It Works

1. **Pi sends packets every 250ms** — either CMD (motor command) or HEARTBEAT (keep-alive)
2. **Arduino responds with ACK** confirming motor states
3. **Watchdog protection:**
   - Arduino stops motors if no packet for **600ms** (connection lost)
   - Pi shows "COMMS LOST" if no ACK for **600ms**
4. **Dual control:** Both touchscreen buttons and GPIO hardware buttons work simultaneously

## Communication Protocol

### Pi → Arduino (6 bytes)
```
[0] Magic (0xAB) | [1] Sequence | [2] Type | [3] Motor A | [4] Motor B | [5] Checksum
```

### Arduino → Pi (5 bytes)
```
[0] Magic (0xBA) | [1] Seq Echo | [2] Motor A State | [3] Motor B State | [4] Checksum
```

## Hardware Configuration

### Arduino Pins
| Pin | Function | Notes |
|-----|----------|-------|
| 2, 3, 8 | LEDs (Red, Green, Heartbeat) | 330Ω resistors to GND |
| 4, 5, 6, 7 | Motor A/B UP/DOWN signals | PWM capable |
| 9, 10, 11, 12, 13 | W5500 (RST, CS, MOSI, MISO, SCK) | SPI interface |

### Raspberry Pi GPIO (BCM)
| GPIO | Function | Notes |
|------|----------|-------|
| 17, 27, 22, 23 | Motor A/B UP/DOWN buttons | Internal pull-up |
| 24, 25, 12 | LEDs (Green, Red, Heartbeat) | 220Ω resistors to GND |

**Network:** Static IP, no DHCP. Direct Ethernet cable between devices.

## Quick Start

### Arduino Setup
1. Open `arduino/motor_control/motor_control.ino` in Arduino IDE
2. Select Board: **Arduino Uno** | Port: your Arduino port
3. Upload and verify serial output shows IP `192.168.10.2`

### Raspberry Pi Setup
```bash
# 1. Set static IP
sudo nmcli con add con-name eth0-static ifname eth0 type ethernet ip4 192.168.10.1/24
sudo nmcli con up eth0-static

# 2. Enable passwordless shutdown
sudo cp sudoers_shutdown /etc/sudoers.d/shutdown
sudo chmod 440 /etc/sudoers.d/shutdown

# 3. Copy files
mkdir -p /home/pi/motor_control
cp motor_daemon.py ui.py /home/pi/motor_control/

# 4. Autostart (optional)
mkdir -p /home/pi/.config/autostart
cp motor_control.desktop /home/pi/.config/autostart/

# 5. Reboot
sudo reboot
```

## Operation

### Controls
- **Touchscreen buttons (▲/▼):** Hold to run motor, release to stop
- **GPIO buttons:** Same function, runs in parallel
- **SHUTDOWN button:** Safe shutdown with confirmation
- **QUIT:** Exit UI (dev/debug only)

### LED Indicators

**Arduino:**
- 🟢 Green ON → Pi connected, heartbeats arriving
- 🔴 Red ON → No heartbeat, motors stopped
- 💙 Heartbeat blinks → Valid packet received

**Raspberry Pi:**
- 🟢 Green ON → Arduino link OK
- 🔴 Red ON → Comms lost or no Ethernet
- 💙 Heartbeat blinks → Each packet sent

## Resistor Values

- **Arduino LEDs:** 330Ω (5V system) → (5.0 - 2.0V) / 10mA ≈ 300Ω
- **Pi LEDs:** 220Ω (3.3V system) → (3.3 - 2.0V) / 8mA ≈ 162Ω
- **Button wiring:** No resistor needed (internal pull-up enabled)

## Console Status Fields

| Field | Meaning |
|-------|---------|
| ETH LINK | Ethernet connection status |
| LATENCY | Round-trip packet time (ms) |
| HB RATE | Heartbeat frequency (target 4.0 Hz) |
| MOTOR A/B | Confirmed motor states from Arduino |
| PKTS LOST | Packet loss counter (resets on reconnect) |
| PI TEMP | CPU temperature (yellow >60°C, red >75°C) |
| UPTIME | System uptime since last boot |

## File Structure

```
v3/
├── arduino/
│   └── motor_control/motor_control.ino    (Upload to Arduino)
└── pi/
    ├── motor_daemon.py                    (UDP daemon)
    ├── ui.py                              (Touchscreen UI)
    ├── motor_control.desktop              (Autostart config)
    └── sudoers_shutdown                   (Sudoers config)
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| UI doesn't launch on boot | Verify path in `motor_control.desktop` matches file location |
| WAITING FOR NETWORK | Check Ethernet: `ip addr show eth0` should show `192.168.10.1/24` |
| COMMS LOST on screen | Ping Arduino: `ping 192.168.10.2` — check Arduino serial output |
| Arduino shows IP 0.0.0.0 | W5500 wiring issue — verify SPI pins (CS=10, RST=9) and 3.3V power |
| GPIO buttons unresponsive | Test: `python3 -c "import RPi.GPIO"` |
| Motors won't stop | Check watchdog timeout — should trigger at 600ms no packet |
| LED very dim | Resistor too high — try 100Ω (keep Pi GPIO ≤16mA) |



## Future Extension

**Fiber media converters** can extend the system up to 20km:
1. Connect media converter at each end of Ethernet cable
2. Run fiber between converters
3. No software changes needed (transparent at physical layer)

---

**Language Composition:** Python (71.9%) | C++ (28.1%)  
**Status:** Production ready with failsafe protection
