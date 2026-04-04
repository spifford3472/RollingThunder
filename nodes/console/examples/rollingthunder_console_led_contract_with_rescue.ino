#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <BLEClient.h>
#include <BLERemoteCharacteristic.h>

// ============================================================
// RollingThunder Console ESP32
//
// Responsibilities:
// - emit raw input events + heartbeat JSON to rt-controller
// - accept controller->console LED contract JSON over serial
// - render controller-owned LED states locally
// - preserve special-case guarded BLE Pi power-cycle rescue path
//
// Notes:
// - rescue mode temporarily overrides primary/cancel LEDs only
// - controller remains authoritative for normal LED meaning
// - no debug Serial output; Serial is reserved for protocol traffic
// ============================================================

// =========================
// Pin map
// =========================
static const int PIN_BACK_BTN    = 4;
static const int PIN_PAGE_BTN    = 5;
static const int PIN_PRIMARY_BTN = 18;
static const int PIN_CANCEL_BTN  = 21;
static const int PIN_MODE_BTN    = 23;
static const int PIN_INFO_BTN    = 33;

static const int PIN_ENC_CLK = 25;
static const int PIN_ENC_DT  = 26;
static const int PIN_ENC_SW  = 27;

static const int PIN_BACK_LED    = 14; // Blue
static const int PIN_PAGE_LED    = 15; // Blue
static const int PIN_PRIMARY_LED = 16; // Green
static const int PIN_CANCEL_LED  = 2;  // Red
static const int PIN_MODE_LED    = 22; // Yellow
static const int PIN_INFO_LED    = 19; // White

// =========================
// BLE trunk target
// =========================
static BLEUUID SERVICE_UUID("7b8f4d10-4b2f-4d1a-9c20-8d8a0f12a001");
static BLEUUID COMMAND_CHAR_UUID("7b8f4d10-4b2f-4d1a-9c20-8d8a0f12a002");
static const char* BLE_LOCAL_NAME = "RollingThunder-Console";
static const char* SHARED_SECRET  = "rollingthunder-2026-key";

// =========================
// Timing
// =========================
static const unsigned long DEBOUNCE_MS          = 20;
static const unsigned long HOLD_MS              = 650;
static const unsigned long HEARTBEAT_MS         = 2000;
static const unsigned long ARM_TIMEOUT_MS       = 5000;
static const unsigned long FLASH_INTERVAL_MS    = 250;
static const unsigned long SHOW_PUSH_STEP_MS    = 250;
static const unsigned long DEFAULT_BLINK_MS     = 400;
static const unsigned long DEFAULT_PULSE_MS     = 900;
static const unsigned long SERIAL_FRAME_MAX_LEN = 700;

// =========================
// Raw protocol identity
// =========================
static const char* PANEL_ID = "panel-v1-main";

// =========================
// Button model
// =========================
struct Button {
  const char* controlId;
  int pin;
  int ledPin;
  bool rawPressed;
  bool stablePressed;
  bool holdSent;
  unsigned long lastRawChangeMs;
  unsigned long pressedAtMs;
};

Button buttons[] = {
  { "btn_back",    PIN_BACK_BTN,    PIN_BACK_LED,    false, false, false, 0, 0 },
  { "btn_page",    PIN_PAGE_BTN,    PIN_PAGE_LED,    false, false, false, 0, 0 },
  { "btn_primary", PIN_PRIMARY_BTN, PIN_PRIMARY_LED, false, false, false, 0, 0 },
  { "btn_cancel",  PIN_CANCEL_BTN,  PIN_CANCEL_LED,  false, false, false, 0, 0 },
  { "btn_mode",    PIN_MODE_BTN,    PIN_MODE_LED,    false, false, false, 0, 0 },
  { "btn_info",    PIN_INFO_BTN,    PIN_INFO_LED,    false, false, false, 0, 0 },
};

static const size_t BUTTON_COUNT = sizeof(buttons) / sizeof(buttons[0]);

static const size_t IDX_BACK    = 0;
static const size_t IDX_PAGE    = 1;
static const size_t IDX_PRIMARY = 2;
static const size_t IDX_CANCEL  = 3;
static const size_t IDX_MODE    = 4;
static const size_t IDX_INFO    = 5;

// =========================
// Encoder state
// =========================
int lastEncClkState = HIGH;
bool encSwRawPressed = false;
bool encSwStablePressed = false;
bool encSwHoldSent = false;
unsigned long encSwLastRawChangeMs = 0;
unsigned long encSwPressedAtMs = 0;

// =========================
// Sequence / heartbeat
// =========================
uint32_t seqCounter = 1;
unsigned long lastHeartbeatMs = 0;

// =========================
// Rescue mode state
// =========================
bool resetArmed = false;
unsigned long resetArmExpiresMs = 0;
unsigned long lastFlashToggleMs = 0;
bool flashOn = false;

// =========================
// BLE scan target
// =========================
BLEAdvertisedDevice* foundDevice = nullptr;

class TargetAdvertisedDeviceCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) override {
    if (advertisedDevice.haveServiceUUID() && advertisedDevice.isAdvertisingService(SERVICE_UUID)) {
      if (foundDevice != nullptr) {
        delete foundDevice;
        foundDevice = nullptr;
      }
      foundDevice = new BLEAdvertisedDevice(advertisedDevice);
      BLEDevice::getScan()->stop();
    }
  }
};

TargetAdvertisedDeviceCallbacks advertisedCallbacks;

// =========================
// LED contract state
// =========================
enum LedMode : uint8_t {
  LED_MODE_OFF = 0,
  LED_MODE_ON,
  LED_MODE_BLINK,
  LED_MODE_PULSE
};

struct LedState {
  int pin;
  LedMode mode;
  unsigned long periodMs;
  bool outputNow;
  unsigned long phaseStartMs;

  bool showPushActive;
  uint8_t showPushPhase;
  unsigned long showPushPhaseStartedMs;
};

LedState leds[BUTTON_COUNT];

// =========================
// Serial input parser state
// =========================
String serialInLine;

// =========================
// Utilities
// =========================
static inline bool readPressed(int pin) {
  return digitalRead(pin) == LOW;
}

int buttonNameToIndex(const String& name) {
  if (name == "back") return (int)IDX_BACK;
  if (name == "page") return (int)IDX_PAGE;
  if (name == "primary") return (int)IDX_PRIMARY;
  if (name == "cancel") return (int)IDX_CANCEL;
  if (name == "mode") return (int)IDX_MODE;
  if (name == "info") return (int)IDX_INFO;
  return -1;
}

LedMode ledModeFromString(const String& mode) {
  if (mode == "off") return LED_MODE_OFF;
  if (mode == "on") return LED_MODE_ON;
  if (mode == "blink") return LED_MODE_BLINK;
  if (mode == "pulse") return LED_MODE_PULSE;
  return (LedMode)255;
}

void emitButtonEvent(const char* controlId, const char* eventType) {
  uint32_t seq = seqCounter++;
  Serial.print("{\"schema\":1,\"event_id\":\"");
  Serial.print(seq);
  Serial.print("\",\"panel_id\":\"");
  Serial.print(PANEL_ID);
  Serial.print("\",\"control_id\":\"");
  Serial.print(controlId);
  Serial.print("\",\"event_type\":\"");
  Serial.print(eventType);
  Serial.print("\",\"seq\":");
  Serial.print(seq);
  Serial.println("}");
}

void emitRotateEvent(int delta) {
  uint32_t seq = seqCounter++;
  Serial.print("{\"schema\":1,\"event_id\":\"");
  Serial.print(seq);
  Serial.print("\",\"panel_id\":\"");
  Serial.print(PANEL_ID);
  Serial.print("\",\"control_id\":\"enc_main\",\"event_type\":\"rotate\",\"value\":");
  Serial.print(delta);
  Serial.print(",\"seq\":");
  Serial.print(seq);
  Serial.println("}");
}

void emitHeartbeat() {
  uint32_t seq = seqCounter++;
  Serial.print("{\"schema\":1,\"type\":\"heartbeat\",\"panel_id\":\"");
  Serial.print(PANEL_ID);
  Serial.print("\",\"seq\":");
  Serial.print(seq);
  Serial.println("}");
}

// =========================
// LED helpers
// =========================
void ledWritePhysical(size_t idx, bool on) {
  leds[idx].outputNow = on;
  digitalWrite(leds[idx].pin, on ? HIGH : LOW);
}

void ledSetPersistent(size_t idx, LedMode mode, unsigned long periodMs) {
  leds[idx].mode = mode;
  leds[idx].periodMs = periodMs;
  leds[idx].phaseStartMs = millis();
  leds[idx].showPushActive = false;
}

void ledResetOne(size_t idx) {
  leds[idx].mode = LED_MODE_OFF;
  leds[idx].periodMs = 0;
  leds[idx].phaseStartMs = millis();
  leds[idx].showPushActive = false;
  leds[idx].showPushPhase = 0;
  leds[idx].showPushPhaseStartedMs = 0;
  ledWritePhysical(idx, false);
}

void ledResetAll() {
  for (size_t i = 0; i < BUTTON_COUNT; i++) {
    ledResetOne(i);
  }
}

void ledShowPush(size_t idx) {
  leds[idx].showPushActive = true;
  leds[idx].showPushPhase = 0;
  leds[idx].showPushPhaseStartedMs = millis();
  ledWritePhysical(idx, false);
}

void applyLedModeToPhysical(size_t idx, unsigned long now) {
  LedState& led = leds[idx];

  if (led.showPushActive) {
    unsigned long elapsed = now - led.showPushPhaseStartedMs;

    if (elapsed >= SHOW_PUSH_STEP_MS) {
      led.showPushPhase++;
      led.showPushPhaseStartedMs = now;

      if (led.showPushPhase >= 3) {
        led.showPushActive = false;
        led.phaseStartMs = now;
      }
    }

    if (led.showPushActive) {
      // phase 0 = off, phase 1 = on, phase 2 = off
      bool v = (led.showPushPhase == 1);
      if (led.outputNow != v) {
        ledWritePhysical(idx, v);
      }
      return;
    }
  }

  switch (led.mode) {
    case LED_MODE_OFF:
      if (led.outputNow) ledWritePhysical(idx, false);
      break;

    case LED_MODE_ON:
      if (!led.outputNow) ledWritePhysical(idx, true);
      break;

    case LED_MODE_BLINK: {
      unsigned long p = (led.periodMs > 0) ? led.periodMs : DEFAULT_BLINK_MS;
      bool v = ((now - led.phaseStartMs) % p) < (p / 2);
      if (led.outputNow != v) ledWritePhysical(idx, v);
      break;
    }

    case LED_MODE_PULSE: {
      unsigned long p = (led.periodMs > 0) ? led.periodMs : DEFAULT_PULSE_MS;
      unsigned long phase = (now - led.phaseStartMs) % p;
      // visually distinct from blink: short on, long off
      bool v = phase < (p / 4);
      if (led.outputNow != v) ledWritePhysical(idx, v);
      break;
    }
  }
}

void updateLedRendering() {
  if (resetArmed) {
    unsigned long now = millis();

    if ((long)(now - resetArmExpiresMs) >= 0) {
      resetArmed = false;
      flashOn = false;
    } else if (now - lastFlashToggleMs >= FLASH_INTERVAL_MS) {
      lastFlashToggleMs = now;
      flashOn = !flashOn;
    }

    // Render all controller LEDs except primary/cancel as normal.
    for (size_t i = 0; i < BUTTON_COUNT; i++) {
      if (i == IDX_PRIMARY || i == IDX_CANCEL) continue;
      applyLedModeToPhysical(i, now);
    }

    // Rescue override on primary/cancel only.
    digitalWrite(PIN_PRIMARY_LED, flashOn ? HIGH : LOW);
    digitalWrite(PIN_CANCEL_LED,  flashOn ? HIGH : LOW);
    return;
  }

  unsigned long now = millis();
  for (size_t i = 0; i < BUTTON_COUNT; i++) {
    applyLedModeToPhysical(i, now);
  }
}

// =========================
// Minimal JSON helpers
// =========================
bool jsonExtractString(const String& src, const String& key, String& out) {
  String token = "\"" + key + "\"";
  int p = src.indexOf(token);
  if (p < 0) return false;
  p = src.indexOf(':', p + token.length());
  if (p < 0) return false;
  p = src.indexOf('"', p);
  if (p < 0) return false;
  int q = src.indexOf('"', p + 1);
  if (q < 0) return false;
  out = src.substring(p + 1, q);
  return true;
}

bool jsonExtractUInt(const String& src, const String& key, unsigned long& out) {
  String token = "\"" + key + "\"";
  int p = src.indexOf(token);
  if (p < 0) return false;
  p = src.indexOf(':', p + token.length());
  if (p < 0) return false;
  p++;
  while (p < (int)src.length() && (src[p] == ' ')) p++;
  int q = p;
  while (q < (int)src.length() && isDigit(src[q])) q++;
  if (q <= p) return false;
  out = src.substring(p, q).toInt();
  return true;
}

bool jsonContainsKey(const String& src, const String& key) {
  return src.indexOf("\"" + key + "\"") >= 0;
}

bool extractObjectForKey(const String& src, const String& key, String& out) {
  String token = "\"" + key + "\"";
  int p = src.indexOf(token);
  if (p < 0) return false;
  p = src.indexOf('{', p + token.length());
  if (p < 0) return false;

  int depth = 0;
  for (int i = p; i < (int)src.length(); i++) {
    if (src[i] == '{') depth++;
    else if (src[i] == '}') {
      depth--;
      if (depth == 0) {
        out = src.substring(p, i + 1);
        return true;
      }
    }
  }
  return false;
}

// =========================
// Serial LED contract parser
// =========================
void handleLedSet(const String& line) {
  String buttonName, modeName;
  unsigned long period = 0;

  if (!jsonExtractString(line, "button", buttonName)) return;
  if (!jsonExtractString(line, "mode", modeName)) return;

  int idx = buttonNameToIndex(buttonName);
  if (idx < 0) return;

  LedMode mode = ledModeFromString(modeName);
  if ((uint8_t)mode == 255) return;

  if (mode == LED_MODE_BLINK || mode == LED_MODE_PULSE) {
    if (!jsonExtractUInt(line, "period_ms", period)) {
      period = (mode == LED_MODE_BLINK) ? DEFAULT_BLINK_MS : DEFAULT_PULSE_MS;
    }
  }

  ledSetPersistent((size_t)idx, mode, period);
}

void handleLedShowPush(const String& line) {
  String buttonName;
  if (!jsonExtractString(line, "button", buttonName)) return;

  int idx = buttonNameToIndex(buttonName);
  if (idx < 0) return;

  ledShowPush((size_t)idx);
}

void handleLedSnapshot(const String& line) {
  const char* names[] = { "back", "page", "primary", "cancel", "mode", "info" };

  // Omitted buttons default to off.
  for (size_t i = 0; i < BUTTON_COUNT; i++) {
    ledSetPersistent(i, LED_MODE_OFF, 0);
  }

  for (size_t i = 0; i < BUTTON_COUNT; i++) {
    String obj;
    if (!extractObjectForKey(line, names[i], obj)) {
      continue;
    }

    String modeName;
    if (!jsonExtractString(obj, "mode", modeName)) {
      continue;
    }

    LedMode mode = ledModeFromString(modeName);
    if ((uint8_t)mode == 255) {
      continue;
    }

    unsigned long period = 0;
    if (mode == LED_MODE_BLINK || mode == LED_MODE_PULSE) {
      if (!jsonExtractUInt(obj, "period_ms", period)) {
        period = (mode == LED_MODE_BLINK) ? DEFAULT_BLINK_MS : DEFAULT_PULSE_MS;
      }
    }

    ledSetPersistent(i, mode, period);
  }
}

void handleLedCommandLine(const String& line) {
  String typeValue;
  if (!jsonExtractString(line, "type", typeValue)) return;
  if (typeValue != "led") return;

  unsigned long schema = 0;
  if (jsonExtractUInt(line, "schema", schema)) {
    if (schema != 1) return;
  }

  String cmd;
  if (!jsonExtractString(line, "cmd", cmd)) return;

  if (cmd == "set") {
    handleLedSet(line);
  } else if (cmd == "show_push") {
    handleLedShowPush(line);
  } else if (cmd == "all_off" || cmd == "reset_leds") {
    ledResetAll();
  } else if (cmd == "snapshot") {
    handleLedSnapshot(line);
  } else if (cmd == "capability_snapshot") {
    // informational only in current firmware; intentionally ignored
  }
}

void processSerialIncoming() {
  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      if (serialInLine.length() > 0) {
        handleLedCommandLine(serialInLine);
        serialInLine = "";
      }
      continue;
    }

    if (serialInLine.length() < SERIAL_FRAME_MAX_LEN) {
      serialInLine += c;
    } else {
      // oversize line: drop until newline
    }
  }
}

// =========================
// Rescue BLE helpers
// =========================
void enterResetArmed() {
  resetArmed = true;
  resetArmExpiresMs = millis() + ARM_TIMEOUT_MS;
  lastFlashToggleMs = 0;
  flashOn = false;
}

void cancelResetArmed() {
  resetArmed = false;
  flashOn = false;
}

bool findTrunkServer() {
  if (foundDevice != nullptr) {
    delete foundDevice;
    foundDevice = nullptr;
  }

  BLEScan* scan = BLEDevice::getScan();
  scan->clearResults();
  scan->setAdvertisedDeviceCallbacks(&advertisedCallbacks, false);
  scan->setActiveScan(true);
  scan->start(3, false);

  return foundDevice != nullptr;
}

bool sendPowerCycleCommand() {
  if (!findTrunkServer()) {
    return false;
  }

  BLEClient* client = BLEDevice::createClient();
  if (client == nullptr) {
    delete foundDevice;
    foundDevice = nullptr;
    return false;
  }

  bool ok = client->connect(foundDevice);
  if (!ok) {
    delete client;
    delete foundDevice;
    foundDevice = nullptr;
    return false;
  }

  BLERemoteService* service = client->getService(SERVICE_UUID);
  if (service == nullptr) {
    client->disconnect();
    delete client;
    delete foundDevice;
    foundDevice = nullptr;
    return false;
  }

  BLERemoteCharacteristic* commandChar = service->getCharacteristic(COMMAND_CHAR_UUID);
  if (commandChar == nullptr) {
    client->disconnect();
    delete client;
    delete foundDevice;
    foundDevice = nullptr;
    return false;
  }

  char cmd[64];
  snprintf(cmd, sizeof(cmd), "RTPC|CYCLE|%s", SHARED_SECRET);
  commandChar->writeValue((uint8_t*)cmd, strlen(cmd), true);

  client->disconnect();
  delete client;
  delete foundDevice;
  foundDevice = nullptr;
  return true;
}

void maybeArmFromCombo() {
  if (!resetArmed &&
      buttons[IDX_BACK].stablePressed &&
      buttons[IDX_PAGE].stablePressed &&
      buttons[IDX_MODE].stablePressed) {
    enterResetArmed();
  }
}

// =========================
// Input update
// =========================
void handleButtonPress(Button& b) {
  if (resetArmed) {
    if (&b == &buttons[IDX_CANCEL]) {
      cancelResetArmed();
      return;
    }

    if (&b == &buttons[IDX_PRIMARY]) {
      sendPowerCycleCommand();
      cancelResetArmed();
      return;
    }
  }

  emitButtonEvent(b.controlId, "press");
}

void handleButtonHold(Button& b) {
  if (resetArmed) {
    return;
  }

  emitButtonEvent(b.controlId, "hold");
}

void updateButton(Button& b) {
  bool raw = readPressed(b.pin);
  unsigned long now = millis();

  if (raw != b.rawPressed) {
    b.rawPressed = raw;
    b.lastRawChangeMs = now;
  }

  if (now - b.lastRawChangeMs < DEBOUNCE_MS) {
    return;
  }

  if (b.stablePressed != b.rawPressed) {
    b.stablePressed = b.rawPressed;

    if (b.stablePressed) {
      b.pressedAtMs = now;
      b.holdSent = false;
      handleButtonPress(b);
      maybeArmFromCombo();
    } else {
      b.holdSent = false;
    }
  }

  if (b.stablePressed && !b.holdSent && (now - b.pressedAtMs >= HOLD_MS)) {
    b.holdSent = true;
    handleButtonHold(b);
  }
}

void updateEncoderRotate() {
  int clkState = digitalRead(PIN_ENC_CLK);

  if (clkState != lastEncClkState) {
    if (clkState == LOW) {
      int dtState = digitalRead(PIN_ENC_DT);
      int delta = (dtState != clkState) ? 1 : -1;

      if (!resetArmed) {
        emitRotateEvent(delta);
      }
    }
    lastEncClkState = clkState;
  }
}

void updateEncoderButton() {
  bool raw = readPressed(PIN_ENC_SW);
  unsigned long now = millis();

  if (raw != encSwRawPressed) {
    encSwRawPressed = raw;
    encSwLastRawChangeMs = now;
  }

  if (now - encSwLastRawChangeMs < DEBOUNCE_MS) {
    return;
  }

  if (encSwStablePressed != encSwRawPressed) {
    encSwStablePressed = encSwRawPressed;

    if (encSwStablePressed) {
      encSwPressedAtMs = now;
      encSwHoldSent = false;
      if (!resetArmed) {
        emitButtonEvent("enc_main", "press");
      }
    } else {
      encSwHoldSent = false;
    }
  }

  if (encSwStablePressed && !encSwHoldSent && (now - encSwPressedAtMs >= HOLD_MS)) {
    encSwHoldSent = true;
    if (!resetArmed) {
      emitButtonEvent("enc_main", "hold");
    }
  }
}

// =========================
// Setup / loop
// =========================
void setupLedState() {
  leds[IDX_BACK]    = { PIN_BACK_LED,    LED_MODE_OFF, 0, false, 0, false, 0, 0 };
  leds[IDX_PAGE]    = { PIN_PAGE_LED,    LED_MODE_OFF, 0, false, 0, false, 0, 0 };
  leds[IDX_PRIMARY] = { PIN_PRIMARY_LED, LED_MODE_OFF, 0, false, 0, false, 0, 0 };
  leds[IDX_CANCEL]  = { PIN_CANCEL_LED,  LED_MODE_OFF, 0, false, 0, false, 0, 0 };
  leds[IDX_MODE]    = { PIN_MODE_LED,    LED_MODE_OFF, 0, false, 0, false, 0, 0 };
  leds[IDX_INFO]    = { PIN_INFO_LED,    LED_MODE_OFF, 0, false, 0, false, 0, 0 };
  ledResetAll();
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_BACK_BTN,    INPUT_PULLUP);
  pinMode(PIN_PAGE_BTN,    INPUT_PULLUP);
  pinMode(PIN_PRIMARY_BTN, INPUT_PULLUP);
  pinMode(PIN_CANCEL_BTN,  INPUT_PULLUP);
  pinMode(PIN_MODE_BTN,    INPUT_PULLUP);
  pinMode(PIN_INFO_BTN,    INPUT_PULLUP);

  pinMode(PIN_ENC_CLK, INPUT_PULLUP);
  pinMode(PIN_ENC_DT,  INPUT_PULLUP);
  pinMode(PIN_ENC_SW,  INPUT_PULLUP);

  pinMode(PIN_BACK_LED,    OUTPUT);
  pinMode(PIN_PAGE_LED,    OUTPUT);
  pinMode(PIN_PRIMARY_LED, OUTPUT);
  pinMode(PIN_CANCEL_LED,  OUTPUT);
  pinMode(PIN_MODE_LED,    OUTPUT);
  pinMode(PIN_INFO_LED,    OUTPUT);

  setupLedState();

  for (size_t i = 0; i < BUTTON_COUNT; i++) {
    buttons[i].rawPressed = readPressed(buttons[i].pin);
    buttons[i].stablePressed = buttons[i].rawPressed;
    buttons[i].holdSent = false;
    buttons[i].lastRawChangeMs = millis();
    buttons[i].pressedAtMs = millis();
  }

  lastEncClkState = digitalRead(PIN_ENC_CLK);
  encSwRawPressed = readPressed(PIN_ENC_SW);
  encSwStablePressed = encSwRawPressed;
  encSwHoldSent = false;
  encSwLastRawChangeMs = millis();
  encSwPressedAtMs = millis();

  BLEDevice::init(BLE_LOCAL_NAME);
  serialInLine.reserve(768);
  lastHeartbeatMs = millis();
}

void loop() {
  processSerialIncoming();

  for (size_t i = 0; i < BUTTON_COUNT; i++) {
    updateButton(buttons[i]);
  }

  updateEncoderRotate();
  updateEncoderButton();
  updateLedRendering();

  unsigned long now = millis();
  if (now - lastHeartbeatMs >= HEARTBEAT_MS) {
    lastHeartbeatMs = now;
    emitHeartbeat();
  }

  delay(1);
}
