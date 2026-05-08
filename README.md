# Motor Control System
## Raspberry Pi 5 + Arduino Uno + W5500 Ethernet

A simple, reliable two-node motor control system.  
The Pi runs a touchscreen UI. The Arduino drives motors.  
They talk to each other over a direct Ethernet cable using UDP.

---

## System Architecture

```
┌──────────────────────┐         UDP / Ethernet          ┌──────────────────────┐
│   Raspberry Pi 5     │◄───────────────────────────────►│   Arduino Uno        │
│                      │                                  │   + W5500 Shield     │
│  ui.py               │   CMD packets  ──────────────►  │                      │
│  motor_daemon.py     │   ACK packets  ◄──────────────  │  motor_control.ino   │
│                      │   Heartbeats   ──────────────►  │                      │
│  IP: 192.168.10.1    │                                  │  IP: 192.168.10.2    │
│  Port: 5001 (recv)   │                                  │  Port: 5000 (recv)   │
└──────────────────────┘                                  └──────────────────────┘
```

### Two threads on the Pi
- **sender_thread** — fires every 100ms. Sends a CMD packet if a button is held,
  otherwise sends a HEARTBEAT packet.
- **receiver_thread** — listens for ACK packets from Arduino. Updates status dict.

### One loop on the Arduino
- Reads incoming UDP packets.
- Validates magic byte + XOR checksum.
- On CMD: sets motor outputs, sends ACK.
- On HEARTBEAT: sends ACK, resets watchdog timer.
- If no valid packet arrives for 500ms → stops all motors immediately.

---

## Packet Format

### Command / Heartbeat (Pi → Arduino) — 6 bytes
```
Byte 0 : Magic    = 0xAB
Byte 1 : Seq      = uint8, wraps 0–255
Byte 2 : Type     = 0x01 (CMD) | 0x02 (HEARTBEAT)
Byte 3 : Motor A  = 0x00 (STOP) | 0x01 (UP) | 0x02 (DOWN)
Byte 4 : Motor B  = 0x00 (STOP) | 0x01 (UP) | 0x02 (DOWN)
Byte 5 : Checksum = XOR of bytes 0–4
```

### ACK / Status (Arduino → Pi) — 5 bytes
```
Byte 0 : Magic    = 0xBA
Byte 1 : Seq Echo = mirrors incoming seq
Byte 2 : Motor A confirmed state
Byte 3 : Motor B confirmed state
Byte 4 : Checksum = XOR of bytes 0–3
```

---

## Pin Assignments

### Arduino Uno

| Pin | Function |
|-----|----------|
| 9   | W5500 RST (reset) |
| 10  | W5500 CS (chip select) |
| 11  | SPI MOSI |
| 12  | SPI MISO |
| 13  | SPI SCK + Heartbeat LED (onboard LED) |
| 4   | Motor A — UP signal |
| 5   | Motor A — DOWN signal |
| 6   | Motor B — UP signal |
| 7   | Motor B — DOWN signal |
| 3.3V | W5500 VCC |
| GND | W5500 GND |

### W5500 Module → Arduino Uno

| W5500 Pin | Arduino Pin |
|-----------|-------------|
| VCC       | 3.3V        |
| GND       | GND         |
| SCLK/SCK  | Pin 13      |
| MOSI      | Pin 11      |
| MISO      | Pin 12      |
| CS/SS     | Pin 10      |
| RST       | Pin 9       |

> No level shifter needed — your W5500 module has 5V-tolerant SPI pins.
> VCC must go to 3.3V (not 5V).

### Motor LEDs (for testing)

| LED    | Arduino Pin | Color suggestion |
|--------|-------------|-----------------|
| A UP   | Pin 4       | Green |
| A DOWN | Pin 5       | Red   |
| B UP   | Pin 6       | Green |
| B DOWN | Pin 7       | Red   |

Wire each LED in series with a 220Ω resistor to GND.

---

## Heartbeat LED Behaviour

The onboard LED on pin 13 blinks briefly (80ms) every time a valid packet  
is received from the Pi. At 10Hz heartbeat rate you'll see it flickering steadily.  
If it stops blinking → the Pi has stopped sending → motors are stopped.

---

## Network Configuration

| Device      | IP Address    | Subnet        |
|-------------|---------------|---------------|
| Raspberry Pi | 192.168.10.1 | 255.255.255.0 |
| Arduino      | 192.168.10.2 | 255.255.255.0 |

Direct Ethernet cable. No router, no DHCP, no internet required.  
Connect them and they find each other immediately at boot.

---

## File Structure

```
motor_control_arduino/
└── motor_control/
    └── motor_control.ino       ← Arduino firmware

motor_control_pi/
├── motor_daemon.py             ← UDP send/receive daemon (background threads)
├── ui.py                       ← Touchscreen UI (tkinter)
├── motor_control.service       ← Systemd service for auto-start
└── dhcpcd_eth0_static.conf    ← Static IP config snippet
```

---

## Deployment Guide

### Arduino

**Requirements:**
- Arduino IDE (https://www.arduino.cc/en/software)
- Ethernet library (included with Arduino IDE by default)

**Steps:**
1. Open Arduino IDE
2. Open `motor_control/motor_control.ino`
3. Go to **Tools → Board** → select **Arduino Uno**
4. Go to **Tools → Port** → select your Arduino's COM/tty port
5. Click **Upload** (→ arrow button)
6. Open **Serial Monitor** at 115200 baud to verify startup message

You should see:
```
Motor Control Node starting...
IP: 192.168.10.2
UDP listening on port 5000
```

---

### Raspberry Pi

**Requirements:**
- Raspberry Pi OS (Bullseye or Bookworm, desktop version)
- Python 3 (pre-installed)
- tkinter (pre-installed with desktop OS)

**Step 1 — Set static IP**

Append the contents of `dhcpcd_eth0_static.conf` to `/etc/dhcpcd.conf`:

```bash
sudo nano /etc/dhcpcd.conf
# Add at the bottom:
interface eth0
static ip_address=192.168.10.1/24
static routers=
static domain_name_servers=
nolink
```

Then reboot or restart dhcpcd:
```bash
sudo systemctl restart dhcpcd
```

Verify:
```bash
ip addr show eth0
# Should show 192.168.10.1
```

**Step 2 — Copy files to Pi**

```bash
mkdir -p /home/pi/motor_control
cp motor_daemon.py /home/pi/motor_control/
cp ui.py           /home/pi/motor_control/
```

**Step 3 — Test manually first**

```bash
cd /home/pi/motor_control
python3 ui.py
```

The UI should launch. Connect the Arduino via Ethernet — the console should  
show CONNECTED within 1 second and the heartbeat LED on Arduino should start blinking.

**Step 4 — Install as systemd service (auto-start on boot)**

```bash
sudo cp motor_control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable motor_control
sudo systemctl start motor_control
```

Check status:
```bash
sudo systemctl status motor_control
```

View live logs:
```bash
journalctl -u motor_control -f
```

---

## Operating Instructions

1. Power on both devices (order doesn't matter)
2. Pi UI launches automatically within ~10 seconds of boot
3. Console shows **CONNECTED** (green) once Ethernet link is up
4. **Press and hold** any motor button to move that motor
5. **Release** to stop
6. Both motors can be moved simultaneously
7. If cable is unplugged: console shows **COMMS LOST** (red), motors stop
8. Reconnect cable: system recovers automatically, no reset needed

---

## Extending to Fiber (Future)

When you're ready to extend the cable distance:

1. Buy two identical **Ethernet-to-fiber media converters** (one for each end)
2. Connect them between the existing cable and the fiber run
3. Power both converters

**No software changes needed.** The converters are transparent Layer 1 devices.  
The Pi and Arduino still see a standard Ethernet connection.

For distances:
- Up to 550m → Multimode fiber (cheaper)
- Up to 20km → Single-mode fiber

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| Arduino Serial shows `IP: 0.0.0.0` | W5500 not connected or wrong CS pin |
| Console shows COMMS LOST always | Check Ethernet cable, check static IP on Pi |
| Heartbeat LED not blinking | Pi not sending; check Pi process is running |
| Buttons don't move motors | Check motor LED wiring and pin numbers |
| UI doesn't start on boot | Run `journalctl -u motor_control` to see errors |
| Permission error on socket | Make sure nothing else is using port 5001 |
