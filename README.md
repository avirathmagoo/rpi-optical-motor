# Motor Control System  —  v3.0
### Raspberry Pi 5  +  Arduino Uno  +  W5500 Ethernet

---

## What This System Does

The Raspberry Pi runs a fullscreen touchscreen UI that lets you control two motors
(currently LEDs for testing). Commands travel over a direct Ethernet cable to an
Arduino Uno, which drives the motor outputs. A heartbeat system ensures motors stop
automatically if communication is ever lost. Hardware buttons and indicator LEDs on
both devices provide physical feedback independent of the screen.

---

## File Structure

```
v3/
├── arduino/
│   └── motor_control/
│       └── motor_control.ino     ← Upload this to Arduino
│
└── pi/
    ├── motor_daemon.py           ← UDP + GPIO background daemon
    ├── ui.py                     ← Touchscreen UI (run this)
    ├── motor_control.desktop     ← Autostart / desktop icon file
    └── sudoers_shutdown          ← Allows shutdown from UI without password
```

---

## System Architecture

```
┌─────────────────────────────────┐     Ethernet cable     ┌──────────────────────────┐
│         Raspberry Pi 5          │◄──────────────────────►│     Arduino Uno          │
│                                 │                         │     + W5500 module       │
│  ui.py          (touchscreen)   │   CMD/HB ──────────►   │                          │
│  motor_daemon.py (background)   │   ACK    ◄──────────   │   motor_control.ino      │
│                                 │                         │                          │
│  IP: 192.168.10.1  port 5001   │                         │  IP: 192.168.10.2        │
│                                 │                         │  port 5000               │
│  GPIO: buttons + 3 LEDs        │                         │  pins: motors + 3 LEDs   │
└─────────────────────────────────┘                         └──────────────────────────┘
```

### How Communication Works

1. Pi sends a packet every **250ms** (4 times per second)
2. If a motor button is held, the packet contains a CMD (motor direction)
3. Otherwise it sends a HEARTBEAT (keep-alive)
4. Arduino receives the packet, blinks the heartbeat LED, resets its watchdog timer,
   and sends back an ACK with the confirmed motor states
5. If Arduino receives **no packet for 600ms** → stops all motors, turns red LED on
6. If Pi receives **no ACK for 600ms** → shows COMMS LOST on screen

### Packet Format

**Pi → Arduino (6 bytes)**
```
[0] Magic:    0xAB
[1] Seq:      0–255 wrapping counter
[2] Type:     0x01=CMD  0x02=HEARTBEAT
[3] Motor A:  0x00=STOP  0x01=UP  0x02=DOWN
[4] Motor B:  0x00=STOP  0x01=UP  0x02=DOWN
[5] Checksum: XOR of bytes 0–4
```

**Arduino → Pi (5 bytes)**
```
[0] Magic:    0xBA
[1] Seq echo: mirrors incoming seq
[2] Motor A confirmed state
[3] Motor B confirmed state
[4] Checksum: XOR of bytes 0–3
```

---

## Pin Assignments

### Arduino Uno

| Pin | Function | Notes |
|-----|----------|-------|
| 2   | LED Red — Disconnected | 330Ω to GND |
| 3   | LED Green — Connected  | 330Ω to GND |
| 4   | Motor A — UP signal    | LED or motor driver |
| 5   | Motor A — DOWN signal  | LED or motor driver |
| 6   | Motor B — UP signal    | LED or motor driver |
| 7   | Motor B — DOWN signal  | LED or motor driver |
| 8   | LED Heartbeat          | 330Ω to GND |
| 9   | W5500 RST              | Direct wire |
| 10  | W5500 CS               | Direct wire |
| 11  | W5500 MOSI (SPI)       | Direct wire |
| 12  | W5500 MISO (SPI)       | Direct wire |
| 13  | W5500 SCK  (SPI)       | Direct wire |
| 3.3V | W5500 VCC            | Must be 3.3V not 5V |
| GND | W5500 GND + LED GNDs  | Common ground |

### Raspberry Pi GPIO (BCM numbering)

| GPIO | Pin# | Function | Notes |
|------|------|----------|-------|
| 17   | 11   | Button Motor A UP   | Internal pull-up, wire to GND |
| 27   | 13   | Button Motor A DOWN | Internal pull-up, wire to GND |
| 22   | 15   | Button Motor B UP   | Internal pull-up, wire to GND |
| 23   | 16   | Button Motor B DOWN | Internal pull-up, wire to GND |
| 24   | 18   | LED Green — Connected    | 220Ω to GND |
| 25   | 22   | LED Red — Disconnected   | 220Ω to GND |
| 12   | 32   | LED Heartbeat            | 220Ω to GND |
| GND  | 6/9/14/20/25/30/34/39 | Common ground | |

---

## Wiring Diagrams

### LED Wiring (same method for all LEDs)

```
Arduino (5V system)          Raspberry Pi (3.3V system)

Pin X ──► [330Ω] ──► LED+ ──► LED- ──► GND
                                        ▲
                                    common GND

Pin X ──► [220Ω] ──► LED+ ──► LED- ──► GND
```

Always connect resistor between the GPIO pin and the LED anode (+).
LED cathode (−) goes to GND. The flat side of the LED base is the cathode.

### Button Wiring (same for all buttons)

```
Raspberry Pi GPIO pin ──────────────────┐
                                        │
                                   [Button]
                                        │
GND ────────────────────────────────────┘
```

No resistor needed. The code enables the internal pull-up resistor.
Button pressed = GPIO reads LOW. Button released = GPIO reads HIGH.

---

## Resistor Values

### Why Resistors Are Needed
LEDs have no internal resistance. Without a resistor they draw too much current
and will burn out immediately, or damage the GPIO pin.

### Arduino LEDs — use 330Ω
- GPIO outputs 5V
- Typical LED forward voltage: ~2.0V (red/green)
- Target safe current: 10mA
- Calculation: (5.0 − 2.0) / 0.010 = 300Ω → nearest standard = **330Ω**

### Raspberry Pi LEDs — use 220Ω
- GPIO outputs 3.3V
- Typical LED forward voltage: ~2.0V
- Target safe current: 8mA
- Calculation: (3.3 − 2.0) / 0.008 = 162Ω → nearest standard = **220Ω**
- Note: max safe current per Pi GPIO pin is 16mA. Never exceed this.

### Safe resistor colour codes
- **220Ω**: Red – Red – Brown – Gold
- **330Ω**: Orange – Orange – Brown – Gold

Using a higher value (e.g. 470Ω) is fine — LED will be slightly dimmer but safer.
Never use a lower value or no resistor.

---

## Network Configuration

| Device | IP Address | Subnet |
|--------|-----------|--------|
| Raspberry Pi | 192.168.10.1 | 255.255.255.0 |
| Arduino | 192.168.10.2 | 255.255.255.0 |

Direct cable, no router, no DHCP. The UI opens even without Ethernet connected.
The daemon retries the socket every 2 seconds in the background until the
interface is up, then connects automatically.

---

## Deployment Guide

### PART 1 — Arduino

**Requirements:** Arduino IDE installed on any computer.
The Ethernet library is included with Arduino IDE by default — no extra installs.

**Steps:**
1. Open Arduino IDE
2. File → Open → select `v3/arduino/motor_control/motor_control.ino`
3. Tools → Board → Arduino Uno
4. Tools → Port → select your Arduino port
5. Click Upload (→)
6. Open Serial Monitor, set baud rate to **115200**

Expected output:
```
Motor Control Node v3.0 starting...
IP address : 192.168.10.2
Listening  : port 5000
Waiting for Pi heartbeat...
```

If IP shows `0.0.0.0` → W5500 wiring issue, check SPI pins and 3.3V power.

---

### PART 2 — Raspberry Pi Initial Setup

#### Step 1 — Static IP (if not already done)
```bash
sudo nmcli con add con-name eth0-static \
  ifname eth0 type ethernet \
  ip4 192.168.10.1/24
sudo nmcli con up eth0-static
```

Verify:
```bash
ip addr show eth0
# Should show: inet 192.168.10.1/24
```

#### Step 2 — Allow Shutdown Without Password
```bash
sudo cp sudoers_shutdown /etc/sudoers.d/shutdown
sudo chmod 440 /etc/sudoers.d/shutdown
```

Test it works:
```bash
sudo shutdown --no-wall -h +0
# This will actually shut down — only test when ready!
```

#### Step 3 — Copy Program Files
```bash
mkdir -p /home/pi/motor_control
cp motor_daemon.py /home/pi/motor_control/
cp ui.py           /home/pi/motor_control/
```

If your username is not `pi`, replace `/home/pi` with `/home/YOUR_USERNAME` everywhere.

#### Step 4 — Test Manually First
```bash
cd /home/pi/motor_control
python3 ui.py
```

The UI should open fullscreen immediately, even without Ethernet connected.
Console will show "WAITING FOR NETWORK" until the cable is plugged in.
Connect Arduino via Ethernet — should show LINK OK within 1 second.

Press Escape to exit fullscreen during testing.
Click QUIT to close the UI.

#### Step 5 — Set Up Desktop Autostart

This makes the UI launch automatically when the Pi desktop loads.

Edit the `.desktop` file — change the username if yours is not `pi`:
```bash
nano motor_control.desktop
# Change the Exec line path if needed
```

Install the autostart entry:
```bash
mkdir -p /home/pi/.config/autostart
cp motor_control.desktop /home/pi/.config/autostart/

# Also add desktop icon (optional)
cp motor_control.desktop /home/pi/Desktop/
chmod +x /home/pi/Desktop/motor_control.desktop
```

#### Step 6 — Reboot and Verify
```bash
sudo reboot
```

After reboot the UI should launch automatically on the desktop within 15 seconds.
No login, no terminal, no manual steps needed.

---

### Username Note
If your Pi username is `melody` (not `pi`), use these paths instead:
```
/home/melody/motor_control/
/home/melody/.config/autostart/
/home/melody/Desktop/
```
And edit `motor_control.desktop` to use `/home/melody/motor_control/ui.py`

---

## Operating the System

### Normal Operation
1. Power on both devices (order does not matter)
2. Pi desktop loads → UI launches automatically fullscreen
3. Console shows WAITING FOR NETWORK, then LINK OK when cable connects
4. Hold any motor button to run that motor
5. Release to stop
6. Both motors can run simultaneously

### UI Buttons
- **▲ UP** — runs motor in UP direction while held
- **▼ DOWN** — runs motor in DOWN direction while held
- **QUIT** — exits the UI (for development/debug use only)
- **⏻ SHUTDOWN PI** — asks for confirmation, then safely shuts down the Pi

### Hardware Buttons (GPIO)
Work in parallel with the touchscreen. Either source can control the motors.
The CMD SOURCE field on the console shows which source sent the last command.

### LED Indicators

**Arduino:**
| LED | Meaning |
|-----|---------|
| Green ON | Pi is connected, heartbeats arriving |
| Red ON | No heartbeat — motors are stopped |
| Heartbeat blink | Every valid packet received |

**Raspberry Pi:**
| LED | Meaning |
|-----|---------|
| Green ON | Arduino responding, link OK |
| Red ON | Comms lost or no Ethernet |
| Heartbeat blink | Each heartbeat packet sent |

### Console Fields Explained
| Field | What it means |
|-------|---------------|
| ETH LINK | Ethernet connection status |
| LATENCY | Round-trip time for last packet (ms) |
| HB RATE | Measured heartbeat send rate (target: 4.0 Hz) |
| LAST PKT AGE | Milliseconds since last ACK received |
| PKTS LOST | Packets lost since last reconnect (resets on reconnect) |
| MOTOR A/B | Confirmed state from Arduino |
| CMD SOURCE | UI (touchscreen) or HW Button (GPIO) |
| PI TEMP | CPU temperature — yellow >60°C, red >75°C |
| CPU LOAD | Processor usage percentage |
| SESSION TIME | Time since last successful connection |
| UPTIME | Pi uptime since last boot |
| HW BUTTONS | Whether GPIO buttons are enabled |
| SOCKET | Whether UDP socket is bound (WAITING if no Ethernet yet) |

---

## Fiber Extension (Future)

When extending with fiber media converters:
1. Connect a media converter at each end of the Ethernet cable
2. Run fiber between them
3. Power both converters

**No software or configuration changes needed.** The converters are transparent
at the physical layer. The Pi and Arduino see a normal Ethernet connection.

- Up to 550m → Multimode fiber (cheaper)
- Up to 20km → Single-mode fiber

---

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| UI doesn't open on boot | Check path in motor_control.desktop matches actual file location |
| UI opens but stays on WAITING | Ethernet interface not up — check `ip addr show eth0` |
| COMMS LOST even with cable | Ping Arduino: `ping 192.168.10.2` — check Arduino serial output |
| Arduino shows IP 0.0.0.0 | W5500 wiring issue — check CS=10, RST=9, VCC=3.3V |
| Shutdown button does nothing | Check sudoers file: `sudo visudo -f /etc/sudoers.d/shutdown` |
| GPIO buttons not working | Check RPi.GPIO installed: `python3 -c "import RPi.GPIO"` |
| LED very dim | Resistor too high — try 100Ω (check current doesn't exceed 16mA on Pi) |
| LED not lighting | Check polarity — flat side of LED base is cathode (goes to GND) |
| Motor LEDs flicker randomly | Bad checksum packets — check Ethernet cable quality |

---

## Precautions

- **Never remove the resistors from LED circuits.** The GPIO pins will be damaged.
- **Never connect W5500 VCC to 5V.** Use the 3.3V pin only.
- **Always test with LEDs before connecting real motors.** Verify direction logic is correct.
- **The Pi GPIO pins are 3.3V only.** Never apply 5V to a GPIO pin — it will permanently damage the Pi.
- **Max current per Pi GPIO pin is 16mA.** The 220Ω resistor keeps this safe.
- **Arduino 5V pin can source up to 200mA total** across all outputs — keep LED count reasonable.
- When wiring, always power off both devices first.
