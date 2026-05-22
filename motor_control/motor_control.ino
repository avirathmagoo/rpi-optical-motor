// ============================================================
//  Motor Control Node — Arduino Uno + W5500  —  v4.1
//
//  Fixes vs v4.0:
//  - FIX (critical): sendAck now replies to udp.remoteIP() /
//    udp.remotePort() instead of hardcoded piIP/PI_PORT.
//    Hardcoded reply fails silently if W5500 ARP cache isn't
//    warm yet — this was the root cause of "COMMS LOST" on the
//    Pi even when heartbeats were arriving at the Arduino.
//  - FIX (critical): Ethernet init loop was broken — the
//    condition (millis() - eth_timeout) < 10000 is always true
//    for ~20 s because eth_timeout = millis() + 10000 makes
//    the subtraction start at -10000. Replaced with a single
//    Ethernet.begin() + hardware-detect check.
//  - FIX: udp.endPacket() return value checked; failures are
//    logged to Serial so they are visible during debugging.
//  - FIX: W5500 hardware reset is now held LOW for a full
//    200 ms (was 100 ms), matching the W5500 datasheet minimum.
//  - FIX: udp.begin() is called after a verified Ethernet init
//    rather than unconditionally — prevents a zombie UDP socket
//    when the W5500 is not responding.
//  - IMPROVEMENT: piIP / PI_PORT are no longer used at all for
//    packet sending. They are kept as constants only for
//    optional future use (e.g. source-IP validation).
//  - IMPROVEMENT: Unknown packet types now send an ACK (so the
//    Pi watchdog stays alive) and log a warning, rather than
//    silently swallowing the packet.
//  - IMPROVEMENT: Serial output at startup confirms the UDP
//    socket is open and ready.
//
//  PIN ASSIGNMENTS  (unchanged from v3.x / v4.0)
//  ───────────────
//  W5500 : CS=10, RST=9, SCK=13, MOSI=11, MISO=12  (VCC=3.3V)
//  Pin 2 : LED Red       — Disconnected / watchdog
//  Pin 3 : LED Green     — Connected
//  Pin 4 : LED Heartbeat — blinks each valid packet
//  Pin 5 : Motor A UP
//  Pin 6 : Motor A DOWN
//  Pin 7 : Motor B UP
//  Pin 8 : Motor B DOWN
//  Pin A0: RELAY output  — active HIGH while FIRE_HOLD arrives
//
//  LED WIRING: Arduino pin → 330Ω → LED(+) → LED(−) → GND
// ============================================================

#include <SPI.h>
#include <Ethernet.h>
#include <EthernetUdp.h>

// ── Network config ───────────────────────────────────────────
byte      mac[]   = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x01 };
IPAddress localIP (192, 168, 10, 2);

// piIP / PI_PORT kept for reference / source-IP validation.
// They are NOT used in sendAck() — replies go to the actual
// sender address captured from each incoming packet.
IPAddress piIP    (192, 168, 10, 1);
const unsigned int PI_PORT    = 5001;
const unsigned int LOCAL_PORT = 5000;

// ── Pins ─────────────────────────────────────────────────────
const uint8_t PIN_W5500_CS      = 10;
const uint8_t PIN_W5500_RST     =  9;
const uint8_t PIN_LED_RED       =  2;
const uint8_t PIN_LED_GREEN     =  3;
const uint8_t PIN_LED_HB        =  4;
const uint8_t PIN_MOTOR_A_UP    =  5;
const uint8_t PIN_MOTOR_A_DOWN  =  6;
const uint8_t PIN_MOTOR_B_UP    =  7;
const uint8_t PIN_MOTOR_B_DOWN  =  8;
const uint8_t PIN_RELAY         = A0;

// ── Packet constants ─────────────────────────────────────────
const uint8_t MAGIC_CMD       = 0xAB;
const uint8_t MAGIC_ACK       = 0xBA;
const uint8_t TYPE_CMD        = 0x01;
const uint8_t TYPE_HB         = 0x02;
const uint8_t TYPE_FIRE_HOLD  = 0x03;
const uint8_t TYPE_FIRE_OFF   = 0x04;
const uint8_t CMD_STOP        = 0x00;
const uint8_t CMD_UP          = 0x01;
const uint8_t CMD_DOWN        = 0x02;
const uint8_t PKT_IN_LEN      = 6;
const uint8_t PKT_OUT_LEN     = 5;

// ── Timing ───────────────────────────────────────────────────
const unsigned long WATCHDOG_MS      = 600;
const unsigned long HB_BLINK_MS      =  60;
const unsigned long FIRE_WATCHDOG_MS = 700;

// ── State ────────────────────────────────────────────────────
EthernetUDP   udp;
bool          udpReady        = false;  // true once udp.begin() succeeds
unsigned long lastPacketMs    = 0;
unsigned long hbLedOnMs       = 0;
unsigned long fireHoldLastMs  = 0;
bool          hbLedOn         = false;
bool          relayActive     = false;
bool          wasConnected    = false;
uint8_t       motorA          = CMD_STOP;
uint8_t       motorB          = CMD_STOP;

// ── Helpers ──────────────────────────────────────────────────

uint8_t xorChecksum(uint8_t *buf, uint8_t len) {
  uint8_t cs = 0;
  for (uint8_t i = 0; i < len; i++) cs ^= buf[i];
  return cs;
}

void stopAllMotors() {
  motorA = CMD_STOP;
  motorB = CMD_STOP;
  // Motor driver inputs are active-LOW — HIGH = off
  digitalWrite(PIN_MOTOR_A_UP,   HIGH);
  digitalWrite(PIN_MOTOR_A_DOWN, HIGH);
  digitalWrite(PIN_MOTOR_B_UP,   HIGH);
  digitalWrite(PIN_MOTOR_B_DOWN, HIGH);
}

void applyMotor(uint8_t pinUp, uint8_t pinDown, uint8_t cmd) {
  digitalWrite(pinUp,   (cmd == CMD_UP)   ? LOW : HIGH);
  digitalWrite(pinDown, (cmd == CMD_DOWN) ? LOW : HIGH);
}

void setRelay(bool on) {
  if (on == relayActive) return;
  relayActive = on;
  digitalWrite(PIN_RELAY, on ? HIGH : LOW);
  Serial.println(on ? F("RELAY ON") : F("RELAY OFF"));
}

void setConnected(bool connected) {
  if (connected == wasConnected) return;
  wasConnected = connected;
  digitalWrite(PIN_LED_GREEN, connected ? HIGH : LOW);
  digitalWrite(PIN_LED_RED,   connected ? LOW  : HIGH);
  Serial.println(connected
    ? F("STATUS: CONNECTED")
    : F("STATUS: DISCONNECTED — motors and relay stopped"));
}

// sendAck — replies to the ACTUAL sender address captured by
// udp.parsePacket(), not to the hardcoded piIP constant.
// This avoids silent ARP failures on the W5500 at startup and
// after reconnection.
void sendAck(uint8_t seqEcho) {
  uint8_t pkt[PKT_OUT_LEN];
  pkt[0] = MAGIC_ACK;
  pkt[1] = seqEcho;
  pkt[2] = motorA;
  pkt[3] = motorB;
  pkt[4] = xorChecksum(pkt, 4);

  // Reply to whoever sent this packet — no ARP lookup needed
  udp.beginPacket(udp.remoteIP(), udp.remotePort());
  udp.write(pkt, PKT_OUT_LEN);
  int result = udp.endPacket();
  if (!result) {
    Serial.println(F("WARNING: ACK send failed (endPacket=0)"));
  }
}

const char* cmdName(uint8_t cmd) {
  if (cmd == CMD_UP)   return "UP";
  if (cmd == CMD_DOWN) return "DOWN";
  return "STOP";
}

// ── Ethernet init (called once from setup, retried if needed) ─
bool initEthernet() {
  // Hard-reset the W5500 — hold LOW ≥200 ms per datasheet
  pinMode(PIN_W5500_RST, OUTPUT);
  digitalWrite(PIN_W5500_RST, LOW);
  delay(200);
  digitalWrite(PIN_W5500_RST, HIGH);
  delay(500);   // Allow W5500 PLL to stabilise after reset

  Ethernet.init(PIN_W5500_CS);
  Ethernet.begin(mac, localIP);
  delay(500);   // Allow link negotiation

  IPAddress ip = Ethernet.localIP();
  if (ip == IPAddress(0, 0, 0, 0)) {
    Serial.println(F("ERROR: Ethernet init failed — IP is 0.0.0.0"));
    Serial.println(F("Check: W5500 CS=10, RST=9, VCC=3.3V (NOT 5V), SPI wiring"));
    return false;
  }

  Serial.print(F("IP address : "));
  Serial.println(ip);
  return true;
}

// ── Setup ─────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println(F("Motor Control Node v4.1 starting..."));

  // Configure all output pins
  uint8_t outPins[] = {
    PIN_MOTOR_A_UP, PIN_MOTOR_A_DOWN,
    PIN_MOTOR_B_UP, PIN_MOTOR_B_DOWN,
    PIN_LED_GREEN, PIN_LED_RED, PIN_LED_HB,
    PIN_RELAY
  };
  for (uint8_t i = 0; i < sizeof(outPins); i++) {
    pinMode(outPins[i], OUTPUT);
    digitalWrite(outPins[i], LOW);
  }

  // Safe initial state — motors off, relay off, red LED on
  stopAllMotors();
  digitalWrite(PIN_RELAY,   LOW);
  digitalWrite(PIN_LED_RED, HIGH);

  // Initialise Ethernet — retry up to 3 times if the W5500
  // doesn't respond (e.g. first power-on brown-out)
  bool ethOk = false;
  for (uint8_t attempt = 1; attempt <= 3; attempt++) {
    Serial.print(F("Ethernet init attempt "));
    Serial.print(attempt);
    Serial.println(F("/3..."));
    if (initEthernet()) {
      ethOk = true;
      break;
    }
    delay(1000);
  }

  if (!ethOk) {
    // Flash red LED rapidly to signal hardware fault.
    // Still enter the main loop so Serial stays responsive.
    Serial.println(F("FATAL: Ethernet init failed after 3 attempts. Check wiring."));
    for (uint8_t i = 0; i < 10; i++) {
      digitalWrite(PIN_LED_RED, LOW);
      delay(100);
      digitalWrite(PIN_LED_RED, HIGH);
      delay(100);
    }
    // udpReady remains false — loop() will spin without parsing
    return;
  }

  Serial.print(F("Listening  : port "));
  Serial.println(LOCAL_PORT);

  udp.begin(LOCAL_PORT);
  udpReady = true;

  Serial.println(F("UDP socket open. Waiting for Pi heartbeat..."));

  unsigned long now  = millis();
  lastPacketMs       = now;
  fireHoldLastMs     = now;
}

// ── Loop ──────────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  Ethernet.maintain();

  // ── Pi comms watchdog ──────────────────────────────────────
  bool connected = (now - lastPacketMs) < WATCHDOG_MS;
  if (!connected) {
    stopAllMotors();
    setRelay(false);
    // Keep fireHoldLastMs current so the FIRE watchdog below
    // doesn't double-trigger an already-cleared relay
    fireHoldLastMs = now;
  }
  setConnected(connected);

  // ── FIRE hold watchdog ─────────────────────────────────────
  // Safety net: if relay is on but FIRE_HOLD packets have
  // stopped arriving, kill the relay regardless of Pi comms state
  if (relayActive && (now - fireHoldLastMs) >= FIRE_WATCHDOG_MS) {
    setRelay(false);
    Serial.println(F("RELAY OFF — FIRE_HOLD timeout (safety)"));
  }

  // ── Heartbeat LED auto-off ─────────────────────────────────
  if (hbLedOn && (now - hbLedOnMs) > HB_BLINK_MS) {
    hbLedOn = false;
    digitalWrite(PIN_LED_HB, LOW);
  }

  // ── Skip packet parsing if UDP socket isn't ready ──────────
  if (!udpReady) return;

  // ── Receive incoming UDP packet ────────────────────────────
  int pktSize = udp.parsePacket();
  if (pktSize <= 0) return;

  // Capture sender address BEFORE any read — remoteIP() /
  // remotePort() are only valid immediately after parsePacket()
  IPAddress      senderIP   = udp.remoteIP();
  unsigned int   senderPort = udp.remotePort();

  if (pktSize < PKT_IN_LEN) {
    // Drain and discard undersized packet
    while (udp.available()) udp.read();
    return;
  }

  uint8_t buf[PKT_IN_LEN];
  int bytesRead = udp.read(buf, PKT_IN_LEN);
  if (bytesRead < PKT_IN_LEN) return;

  // ── Magic byte check ──
  if (buf[0] != MAGIC_CMD) return;

  // ── Checksum check ──
  if (xorChecksum(buf, PKT_IN_LEN - 1) != buf[PKT_IN_LEN - 1]) {
    Serial.println(F("Bad checksum — dropped"));
    return;
  }

  uint8_t seq  = buf[1];
  uint8_t type = buf[2];

  // Valid packet — reset comms watchdog and blink heartbeat LED
  lastPacketMs = now;
  hbLedOn      = true;
  hbLedOnMs    = now;
  digitalWrite(PIN_LED_HB, HIGH);

  // ── Dispatch by type ──────────────────────────────────────

  if (type == TYPE_HB) {
    sendAck(seq);
    return;
  }

  if (type == TYPE_CMD) {
    uint8_t cmdA = buf[3];
    uint8_t cmdB = buf[4];
    if (cmdA > CMD_DOWN || cmdB > CMD_DOWN) {
      Serial.println(F("Invalid CMD values — dropped"));
      return;
    }
    motorA = cmdA;
    motorB = cmdB;
    applyMotor(PIN_MOTOR_A_UP,   PIN_MOTOR_A_DOWN,   motorA);
    applyMotor(PIN_MOTOR_B_UP,   PIN_MOTOR_B_DOWN,   motorB);
    Serial.print(F("CMD  A="));
    Serial.print(cmdName(motorA));
    Serial.print(F("  B="));
    Serial.println(cmdName(motorB));
    sendAck(seq);
    return;
  }

  if (type == TYPE_FIRE_HOLD) {
    fireHoldLastMs = now;
    setRelay(true);
    sendAck(seq);
    return;
  }

  if (type == TYPE_FIRE_OFF) {
    setRelay(false);
    sendAck(seq);
    return;
  }

  // Unknown type — still ACK so the Pi watchdog stays alive,
  // but log a warning for debugging
  Serial.print(F("Unknown packet type 0x"));
  Serial.println(type, HEX);
  sendAck(seq);
}
