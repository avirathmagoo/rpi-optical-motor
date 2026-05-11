// ============================================================
//  Motor Control Node — Arduino Uno + W5500  —  v3.2
//
//  Changes from v3.1:
//  - Added FIRE command (TYPE_FIRE 0x03): activates relay for
//    a set duration, then releases automatically
//  - RELAY_DURATION_MS: adjust this to set how long the relay
//    stays energised per FIRE pulse (default 500 ms)
//
//  PIN ASSIGNMENTS
//  ───────────────
//  W5500 : CS=10, RST=9, SCK=13, MOSI=11, MISO=12  (VCC=3.3V)
//  Pin 2 : LED Red       — Disconnected / watchdog
//  Pin 3 : LED Green     — Connected
//  Pin 4 : Motor A UP
//  Pin 5 : Motor A DOWN
//  Pin 6 : Motor B UP
//  Pin 7 : Motor B DOWN
//  Pin 8 : LED Heartbeat — blinks each valid packet
//  Pin A0: RELAY output  — active HIGH for RELAY_DURATION_MS
//
//  LED WIRING: Arduino pin → 330Ω → LED(+) → LED(−) → GND
// ============================================================

#include <SPI.h>
#include <Ethernet.h>
#include <EthernetUdp.h>

// ── Network config ───────────────────────────────────────────
byte      mac[]   = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x01 };
IPAddress localIP (192, 168, 10, 2);
IPAddress piIP    (192, 168, 10, 1);

const unsigned int LOCAL_PORT = 5000;
const unsigned int PI_PORT    = 5001;

// ── Pins ─────────────────────────────────────────────────────
const uint8_t PIN_W5500_CS      = 10;
const uint8_t PIN_W5500_RST     =  9;
const uint8_t PIN_LED_RED       =  2;
const uint8_t PIN_LED_GREEN     =  3;
const uint8_t PIN_MOTOR_A_UP    =  5;
const uint8_t PIN_MOTOR_A_DOWN  =  6;
const uint8_t PIN_MOTOR_B_UP    =  7;
const uint8_t PIN_MOTOR_B_DOWN  =  8;
const uint8_t PIN_LED_HB        =  4;
const uint8_t PIN_RELAY         = A0;   // Relay output pin

// ── Relay timing — ADJUST THIS to set relay pulse duration ───
const unsigned long RELAY_DURATION_MS = 500;   // milliseconds

// ── Packet constants ─────────────────────────────────────────
const uint8_t MAGIC_CMD   = 0xAB;
const uint8_t MAGIC_ACK   = 0xBA;
const uint8_t TYPE_CMD    = 0x01;
const uint8_t TYPE_HB     = 0x02;
const uint8_t TYPE_FIRE   = 0x03;   // One-shot relay trigger
const uint8_t CMD_STOP    = 0x00;
const uint8_t CMD_UP      = 0x01;
const uint8_t CMD_DOWN    = 0x02;
const uint8_t PKT_IN_LEN  = 6;
const uint8_t PKT_OUT_LEN = 5;

// ── Timing ───────────────────────────────────────────────────
const unsigned long WATCHDOG_MS  = 600;
const unsigned long HB_BLINK_MS  =  60;

// ── State ────────────────────────────────────────────────────
EthernetUDP   udp;
unsigned long lastPacketMs  = 0;
unsigned long hbLedOnMs     = 0;
unsigned long relayOnMs     = 0;
bool          hbLedOn       = false;
bool          relayActive   = false;
bool          wasConnected  = false;
uint8_t       motorA        = CMD_STOP;
uint8_t       motorB        = CMD_STOP;

// ── Helpers ──────────────────────────────────────────────────

uint8_t xorChecksum(uint8_t *buf, uint8_t len) {
  uint8_t cs = 0;
  for (uint8_t i = 0; i < len; i++) cs ^= buf[i];
  return cs;
}

void stopAllMotors() {
  motorA = CMD_STOP;
  motorB = CMD_STOP;
  digitalWrite(PIN_MOTOR_A_UP,   HIGH);
  digitalWrite(PIN_MOTOR_A_DOWN, HIGH);
  digitalWrite(PIN_MOTOR_B_UP,   HIGH);
  digitalWrite(PIN_MOTOR_B_DOWN, HIGH);
}

void applyMotor(uint8_t pinUp, uint8_t pinDown, uint8_t cmd) {
  digitalWrite(pinUp,   (cmd == CMD_UP)   ? LOW : HIGH);
  digitalWrite(pinDown, (cmd == CMD_DOWN) ? LOW : HIGH);
}

void fireRelay() {
  if (relayActive) return;   // Ignore if already firing (one-shot guard)
  relayActive = true;
  relayOnMs   = millis();
  digitalWrite(PIN_RELAY, HIGH);
  Serial.println(F("FIRE — relay ON"));
}

void setConnected(bool connected) {
  if (connected == wasConnected) return;
  wasConnected = connected;
  digitalWrite(PIN_LED_GREEN, connected ? HIGH : LOW);
  digitalWrite(PIN_LED_RED,   connected ? LOW  : HIGH);
  if (connected) {
    Serial.println(F("STATUS: CONNECTED"));
  } else {
    Serial.println(F("STATUS: DISCONNECTED — motors stopped"));
  }
}

void sendAck(uint8_t seqEcho) {
  uint8_t pkt[PKT_OUT_LEN];
  pkt[0] = MAGIC_ACK;
  pkt[1] = seqEcho;
  pkt[2] = motorA;
  pkt[3] = motorB;
  pkt[4] = xorChecksum(pkt, 4);
  udp.beginPacket(piIP, PI_PORT);
  udp.write(pkt, PKT_OUT_LEN);
  udp.endPacket();
}

const char* cmdName(uint8_t cmd) {
  if (cmd == CMD_UP)   return "UP";
  if (cmd == CMD_DOWN) return "DOWN";
  return "STOP";
}

// ── Setup ─────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println(F("Motor Control Node v3.2 starting..."));

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

  // Safe start state
  stopAllMotors();
  digitalWrite(PIN_RELAY,   LOW);    // Relay off
  digitalWrite(PIN_LED_RED, HIGH);   // Red until Pi connects

  // Reset W5500
  pinMode(PIN_W5500_RST, OUTPUT);
  digitalWrite(PIN_W5500_RST, LOW);
  delay(100);
  digitalWrite(PIN_W5500_RST, HIGH);
  delay(500);

  Ethernet.init(PIN_W5500_CS);
  Ethernet.begin(mac, localIP);
  delay(500);

  Serial.print(F("IP address       : "));
  Serial.println(Ethernet.localIP());
  Serial.print(F("UDP port         : "));
  Serial.println(LOCAL_PORT);
  Serial.print(F("Relay pin        : A0"));
  Serial.println();
  Serial.print(F("Relay duration ms: "));
  Serial.println(RELAY_DURATION_MS);
  Serial.println(F("Waiting for Pi heartbeat..."));

  udp.begin(LOCAL_PORT);
  lastPacketMs = millis();
}

// ── Loop ──────────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  // ── IMPORTANT: must call every loop for W5500 link maintenance ──
  Ethernet.maintain();

  // ── Watchdog ──
  bool connected = (now - lastPacketMs) < WATCHDOG_MS;
  if (!connected) stopAllMotors();
  setConnected(connected);

  // ── Heartbeat LED off ──
  if (hbLedOn && (now - hbLedOnMs > HB_BLINK_MS)) {
    hbLedOn = false;
    digitalWrite(PIN_LED_HB, LOW);
  }

  // ── Relay auto-release ──
  if (relayActive && (now - relayOnMs >= RELAY_DURATION_MS)) {
    relayActive = false;
    digitalWrite(PIN_RELAY, LOW);
    Serial.println(F("FIRE — relay OFF"));
  }

  // ── Check for incoming UDP packet ──
  int pktSize = udp.parsePacket();
  if (pktSize <= 0) return;
  if (pktSize < PKT_IN_LEN) {
    while (udp.available()) udp.read();
    return;
  }

  uint8_t buf[PKT_IN_LEN];
  int bytesRead = udp.read(buf, PKT_IN_LEN);
  if (bytesRead < PKT_IN_LEN) return;

  if (buf[0] != MAGIC_CMD) return;

  if (xorChecksum(buf, PKT_IN_LEN - 1) != buf[PKT_IN_LEN - 1]) {
    Serial.println(F("Bad checksum — dropped"));
    return;
  }

  uint8_t seq  = buf[1];
  uint8_t type = buf[2];

  // Valid packet — reset watchdog, blink heartbeat LED
  lastPacketMs = now;
  hbLedOn      = true;
  hbLedOnMs    = now;
  digitalWrite(PIN_LED_HB, HIGH);

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
    applyMotor(PIN_MOTOR_A_UP, PIN_MOTOR_A_DOWN, motorA);
    applyMotor(PIN_MOTOR_B_UP, PIN_MOTOR_B_DOWN, motorB);

    Serial.print(F("CMD  A="));
    Serial.print(cmdName(motorA));
    Serial.print(F("  B="));
    Serial.println(cmdName(motorB));

    sendAck(seq);
    return;
  }

  if (type == TYPE_FIRE) {
    fireRelay();
    sendAck(seq);
    return;
  }
}
