// ============================================================
//  Motor Control Node — Arduino Uno + W5500
//  Controls 2 motors (LEDs for now) via UDP commands from Pi
//  Heartbeat watchdog: stops motors if no packet for 500ms
// ============================================================

#include <SPI.h>
#include <Ethernet.h>
#include <EthernetUdp.h>

// ── Network config ──────────────────────────────────────────
byte mac[]        = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x01 };
IPAddress localIP (192, 168, 10, 2);
IPAddress piIP    (192, 168, 10, 1);
unsigned int localPort = 5000;
unsigned int piPort    = 5001;

// ── Pin assignments ─────────────────────────────────────────
#define PIN_CS          10   // W5500 chip select
#define PIN_RST          9   // W5500 reset
#define PIN_HEARTBEAT   13   // Onboard LED — blinks on heartbeat
#define PIN_MOTOR_A_UP   4   // Motor A UP   (LED)
#define PIN_MOTOR_A_DOWN 5   // Motor A DOWN (LED)
#define PIN_MOTOR_B_UP   6   // Motor B UP   (LED)
#define PIN_MOTOR_B_DOWN 7   // Motor B DOWN (LED)

// ── Packet constants ─────────────────────────────────────────
#define MAGIC_CMD   0xAB
#define MAGIC_ACK   0xBA
#define TYPE_CMD    0x01
#define TYPE_HB     0x02
#define CMD_STOP    0x00
#define CMD_UP      0x01
#define CMD_DOWN    0x02
#define PKT_IN_LEN  6
#define PKT_OUT_LEN 5

// ── Timing ───────────────────────────────────────────────────
#define WATCHDOG_MS     500   // Stop motors if silent for this long
#define HB_BLINK_MS      80   // How long heartbeat LED stays ON

// ── State ────────────────────────────────────────────────────
EthernetUDP udp;
unsigned long lastPacketMs  = 0;
unsigned long hbLedOnMs     = 0;
bool          hbLedState    = false;
uint8_t       motorAState   = CMD_STOP;
uint8_t       motorBState   = CMD_STOP;

// ── Helpers ──────────────────────────────────────────────────
uint8_t calcChecksum(uint8_t *buf, uint8_t len) {
  uint8_t cs = 0;
  for (uint8_t i = 0; i < len; i++) cs ^= buf[i];
  return cs;
}

void stopAllMotors() {
  motorAState = CMD_STOP;
  motorBState = CMD_STOP;
  digitalWrite(PIN_MOTOR_A_UP,   LOW);
  digitalWrite(PIN_MOTOR_A_DOWN, LOW);
  digitalWrite(PIN_MOTOR_B_UP,   LOW);
  digitalWrite(PIN_MOTOR_B_DOWN, LOW);
}

void applyMotor(uint8_t pinUp, uint8_t pinDown, uint8_t cmd) {
  digitalWrite(pinUp,   cmd == CMD_UP   ? HIGH : LOW);
  digitalWrite(pinDown, cmd == CMD_DOWN ? HIGH : LOW);
}

void sendAck(uint8_t seqEcho) {
  uint8_t pkt[PKT_OUT_LEN];
  pkt[0] = MAGIC_ACK;
  pkt[1] = seqEcho;
  pkt[2] = motorAState;
  pkt[3] = motorBState;
  pkt[4] = calcChecksum(pkt, 4);

  udp.beginPacket(piIP, piPort);
  udp.write(pkt, PKT_OUT_LEN);
  udp.endPacket();
}

void blinkHeartbeat() {
  hbLedState = true;
  hbLedOnMs  = millis();
  digitalWrite(PIN_HEARTBEAT, HIGH);
}

// ── Setup ────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println(F("Motor Control Node starting..."));

  // Motor / LED pins
  pinMode(PIN_MOTOR_A_UP,   OUTPUT);
  pinMode(PIN_MOTOR_A_DOWN, OUTPUT);
  pinMode(PIN_MOTOR_B_UP,   OUTPUT);
  pinMode(PIN_MOTOR_B_DOWN, OUTPUT);
  pinMode(PIN_HEARTBEAT,    OUTPUT);
  stopAllMotors();

  // Reset W5500
  pinMode(PIN_RST, OUTPUT);
  digitalWrite(PIN_RST, LOW);
  delay(50);
  digitalWrite(PIN_RST, HIGH);
  delay(200);

  Ethernet.init(PIN_CS);
  Ethernet.begin(mac, localIP);
  delay(500);

  Serial.print(F("IP: "));
  Serial.println(Ethernet.localIP());

  udp.begin(localPort);
  Serial.println(F("UDP listening on port 5000"));

  lastPacketMs = millis();  // Don't trigger watchdog immediately
}

// ── Loop ─────────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  // ── Watchdog: stop motors if Pi went silent ──
  if (now - lastPacketMs > WATCHDOG_MS) {
    stopAllMotors();
  }

  // ── Heartbeat LED off after blink duration ──
  if (hbLedState && (now - hbLedOnMs > HB_BLINK_MS)) {
    hbLedState = false;
    digitalWrite(PIN_HEARTBEAT, LOW);
  }

  // ── Receive UDP packet ──
  int pktSize = udp.parsePacket();
  if (pktSize < PKT_IN_LEN) return;

  uint8_t buf[PKT_IN_LEN];
  udp.read(buf, PKT_IN_LEN);

  // Validate magic byte
  if (buf[0] != MAGIC_CMD) return;

  // Validate checksum (XOR of bytes 0–4)
  if (calcChecksum(buf, PKT_IN_LEN - 1) != buf[PKT_IN_LEN - 1]) {
    Serial.println(F("Bad checksum, dropping packet"));
    return;
  }

  uint8_t seq  = buf[1];
  uint8_t type = buf[2];

  lastPacketMs = now;   // Valid packet — reset watchdog
  blinkHeartbeat();     // Visual indicator

  if (type == TYPE_HB) {
    // Heartbeat only — just ACK, don't change motors
    sendAck(seq);
    return;
  }

  if (type == TYPE_CMD) {
    uint8_t cmdA = buf[3];
    uint8_t cmdB = buf[4];

    // Validate command values
    if (cmdA > CMD_DOWN || cmdB > CMD_DOWN) return;

    motorAState = cmdA;
    motorBState = cmdB;

    applyMotor(PIN_MOTOR_A_UP, PIN_MOTOR_A_DOWN, motorAState);
    applyMotor(PIN_MOTOR_B_UP, PIN_MOTOR_B_DOWN, motorBState);

    Serial.print(F("A="));
    Serial.print(motorAState);
    Serial.print(F(" B="));
    Serial.println(motorBState);

    sendAck(seq);
  }
}
