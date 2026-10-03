/*
 * VoltGuide Station Prototype Firmware (ESP32)
 * ============================================
 * Hardware Interface:
 *   - Board: ESP32 DevKit / ESP32-WROOM-32
 *   - GPIO25: MOSFET Gate Control (IRFZ44N Low-Voltage Load Switch)
 *   - GPIO26: GREEN LED (Station State: AVAILABLE)
 *   - GPIO27: BLUE LED  (Station State: CHARGING)
 *   - GPIO14: WHITE LED (Station State: COMPLETE)
 *   - GPIO12: RED LED   (Station State: FAULT)
 *   - GPIO32: Physical Fault Button (Active-LOW, INPUT_PULLUP)
 *
 * Telemetry Contract:
 *   - Posts to: POST http://YOUR_COMPUTER_IP:8000/station/telemetry/ingest
 *   - Station ID: "7" (Grand Mercure Mysore)
 *   - Source: "HARDWARE"
 *   - Telemetry Quality: "NOMINAL" (No INA219 current sensor connected)
 *   - Physical State: REAL (ESP32 Controller + MOSFET Logic + Physical Fault Button)
 *
 * Serial Commands (115200 baud):
 *   - START     : Transitions AVAILABLE -> CHARGING
 *   - STOP      : Transitions CHARGING  -> AVAILABLE
 *   - COMPLETE  : Transitions CHARGING  -> COMPLETE
 *   - RESTORE   : Transitions FAULT / COMPLETE -> AVAILABLE
 *   - STATUS    : Prints current state, electrical metrics, and Wi-Fi / backend diagnostics
 */

#include <WiFi.h>
#include <HTTPClient.h>

// -----------------------------------------------------------------------------
// Network & Backend Configuration
// -----------------------------------------------------------------------------
// Replace with your local Wi-Fi credentials
const char* WIFI_SSID     = "yoyoy";
const char* WIFI_PASSWORD = "11111111";

// Replace YOUR_COMPUTER_IP with the local IP of the computer running FastAPI (e.g. "http://192.168.1.50:8000")
// Do NOT use "localhost" or "127.0.0.1" because on ESP32 that refers to the microcontroller itself.
const char* API_BASE_URL  = "http://10.244.124.194";
const char* STATION_ID    = "7";

// -----------------------------------------------------------------------------
// GPIO Pin Assignments
// -----------------------------------------------------------------------------
const int PIN_MOSFET_GATE    = 25; // IRFZ44N gate control (logic output for testing)
const int PIN_LED_AVAILABLE  = 26; // Green LED
const int PIN_LED_CHARGING   = 27; // Blue LED
const int PIN_LED_COMPLETE   = 14; // White LED
const int PIN_LED_FAULT      = 12; // Red LED
const int PIN_FAULT_BUTTON   = 32; // Physical emergency fault button (INPUT_PULLUP)

// -----------------------------------------------------------------------------
// Station State Machine
// -----------------------------------------------------------------------------
enum StationState {
  STATE_AVAILABLE,
  STATE_CHARGING,
  STATE_COMPLETE,
  STATE_FAULT
};

StationState currentState = STATE_AVAILABLE;
String faultReason = "";

// -----------------------------------------------------------------------------
// Electrical & Session Tracking (Nominal Baseline)
// -----------------------------------------------------------------------------
const float NOMINAL_VOLTAGE_V = 400.0f; // Nominal DC bus voltage (Volts)
const float NOMINAL_CURRENT_A = 125.0f; // Nominal charging current (Amps)
const float NOMINAL_POWER_W   = 50000.0f; // Nominal active power (Watts)

unsigned long sessionStartTimeMs = 0;
unsigned long lastTelemetryPostMs = 0;
const unsigned long TELEMETRY_INTERVAL_MS = 3000; // Post telemetry every 3s

float accumulatedEnergyWh = 0.0f;
unsigned long lastEnergyCalcMs = 0;

// Button debounce tracking
int lastButtonState = HIGH;
unsigned long lastDebounceTimeMs = 0;
const unsigned long DEBOUNCE_DELAY_MS = 50;

// Serial input buffer
String inputCommandBuffer = "";

// -----------------------------------------------------------------------------
// Forward Declarations
// -----------------------------------------------------------------------------
void applyHardwareOutputs(StationState state);
void handleSerialCommands();
void checkPhysicalFaultButton();
void updateEnergyAccumulation();
void sendTelemetryToBackend();
void printStatus();
const char* getStateString(StationState state);
const char* getBackendStateString(StationState state);

// -----------------------------------------------------------------------------
// Setup
// -----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println();
  Serial.println(F("=================================================="));
  Serial.println(F("   VoltGuide EV Station Prototype - ESP32 Node   "));
  Serial.println(F("=================================================="));
  Serial.println(F("Hardware Profile:"));
  Serial.println(F("  - Controller: ESP32 DevKit (REAL)"));
  Serial.println(F("  - Actuator  : GPIO25 MOSFET Gate Switch (REAL)"));
  Serial.println(F("  - Telemetry : NOMINAL / ESTIMATED (No INA219 sensor)"));
  Serial.println(F("  - Station ID: 7 (Grand Mercure Mysore)"));
  Serial.println(F("--------------------------------------------------"));

  // Initialize GPIO directions
  pinMode(PIN_MOSFET_GATE, OUTPUT);
  pinMode(PIN_LED_AVAILABLE, OUTPUT);
  pinMode(PIN_LED_CHARGING, OUTPUT);
  pinMode(PIN_LED_COMPLETE, OUTPUT);
  pinMode(PIN_LED_FAULT, OUTPUT);
  pinMode(PIN_FAULT_BUTTON, INPUT_PULLUP);

  // Apply default boot state: AVAILABLE
  applyHardwareOutputs(STATE_AVAILABLE);

  // Initiate non-blocking Wi-Fi connection
  Serial.print(F("[Wi-Fi] Connecting to "));
  Serial.println(WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.println(F("[System] Ready. Enter commands: START, STOP, COMPLETE, RESTORE, STATUS"));
}

// -----------------------------------------------------------------------------
// Main Loop
// -----------------------------------------------------------------------------
void loop() {
  // 1. Monitor physical emergency fault button (authoritative hardware input)
  checkPhysicalFaultButton();

  // 2. Process incoming serial operator commands
  handleSerialCommands();

  // 3. Update nominal session energy accumulation during CHARGING
  updateEnergyAccumulation();

  // 4. Periodically transmit hardware telemetry to FastAPI backend
  unsigned long now = millis();
  if (now - lastTelemetryPostMs >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryPostMs = now;
    sendTelemetryToBackend();
  }

  yield();
}

// -----------------------------------------------------------------------------
// Hardware Output State Actuation
// -----------------------------------------------------------------------------
void applyHardwareOutputs(StationState state) {
  currentState = state;

  switch (state) {
    case STATE_AVAILABLE:
      digitalWrite(PIN_LED_AVAILABLE, HIGH); // Green ON
      digitalWrite(PIN_LED_CHARGING,  LOW);  // Blue OFF
      digitalWrite(PIN_LED_COMPLETE,  LOW);  // White OFF
      digitalWrite(PIN_LED_FAULT,     LOW);  // Red OFF
      digitalWrite(PIN_MOSFET_GATE,   LOW);  // MOSFET gate LOW (Load isolated)
      faultReason = "";
      break;

    case STATE_CHARGING:
      digitalWrite(PIN_LED_AVAILABLE, LOW);  // Green OFF
      digitalWrite(PIN_LED_CHARGING,  HIGH); // Blue ON
      digitalWrite(PIN_LED_COMPLETE,  LOW);  // White OFF
      digitalWrite(PIN_LED_FAULT,     LOW);  // Red OFF
      digitalWrite(PIN_MOSFET_GATE,   HIGH); // MOSFET gate HIGH (Load energized)
      sessionStartTimeMs = millis();
      lastEnergyCalcMs = millis();
      faultReason = "";
      break;

    case STATE_COMPLETE:
      digitalWrite(PIN_LED_AVAILABLE, LOW);  // Green OFF
      digitalWrite(PIN_LED_CHARGING,  LOW);  // Blue OFF
      digitalWrite(PIN_LED_COMPLETE,  HIGH); // White ON
      digitalWrite(PIN_LED_FAULT,     LOW);  // Red OFF
      digitalWrite(PIN_MOSFET_GATE,   LOW);  // MOSFET gate LOW (Load isolated)
      break;

    case STATE_FAULT:
      digitalWrite(PIN_LED_AVAILABLE, LOW);  // Green OFF
      digitalWrite(PIN_LED_CHARGING,  LOW);  // Blue OFF
      digitalWrite(PIN_LED_COMPLETE,  LOW);  // White OFF
      digitalWrite(PIN_LED_FAULT,     HIGH); // Red ON
      digitalWrite(PIN_MOSFET_GATE,   LOW);  // MOSFET gate LOW (Immediate cutoff)
      break;
  }
}

// -----------------------------------------------------------------------------
// Physical Fault Button Handler (Authoritative Hardware Signal)
// -----------------------------------------------------------------------------
void checkPhysicalFaultButton() {
  int reading = digitalRead(PIN_FAULT_BUTTON);

  // Detect state change for debouncing
  if (reading != lastButtonState) {
    lastDebounceTimeMs = millis();
  }

  if ((millis() - lastDebounceTimeMs) > DEBOUNCE_DELAY_MS) {
    // If button is held LOW (pressed) and not already in FAULT
    if (reading == LOW && currentState != STATE_FAULT) {
      Serial.println(F("\n[HARDWARE ALERT] Physical Emergency Fault Button Pressed (GPIO32)!"));
      faultReason = "MANUAL_FAULT";
      applyHardwareOutputs(STATE_FAULT);
      printStatus();
      // Transmit immediate telemetry update on fault trigger
      sendTelemetryToBackend();
    }
  }

  lastButtonState = reading;
}

// -----------------------------------------------------------------------------
// Nominal Energy Integration
// -----------------------------------------------------------------------------
void updateEnergyAccumulation() {
  if (currentState == STATE_CHARGING) {
    unsigned long now = millis();
    if (lastEnergyCalcMs > 0 && now > lastEnergyCalcMs) {
      float deltaHours = (now - lastEnergyCalcMs) / 3600000.0f;
      accumulatedEnergyWh += (NOMINAL_POWER_W * deltaHours);
    }
    lastEnergyCalcMs = now;
  }
}

// -----------------------------------------------------------------------------
// Serial Operator Commands Interface
// -----------------------------------------------------------------------------
void handleSerialCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      inputCommandBuffer.trim();
      inputCommandBuffer.toUpperCase();

      if (inputCommandBuffer.length() > 0) {
        Serial.print(F(">> Command Received: "));
        Serial.println(inputCommandBuffer);

        if (inputCommandBuffer == "START") {
          if (currentState == STATE_AVAILABLE) {
            Serial.println(F("[State Transition] AVAILABLE -> CHARGING (MOSFET ON)"));
            accumulatedEnergyWh = 0.0f;
            applyHardwareOutputs(STATE_CHARGING);
          } else if (currentState == STATE_FAULT) {
            Serial.println(F("[Command Rejected] Cannot start: Station is in FAULT. Send RESTORE first."));
          } else {
            Serial.print(F("[Command Rejected] Station already in state: "));
            Serial.println(getStateString(currentState));
          }
        }
        else if (inputCommandBuffer == "STOP") {
          if (currentState == STATE_CHARGING) {
            Serial.println(F("[State Transition] CHARGING -> AVAILABLE (MOSFET OFF)"));
            applyHardwareOutputs(STATE_AVAILABLE);
          } else {
            Serial.println(F("[Command Rejected] Station is not actively charging."));
          }
        }
        else if (inputCommandBuffer == "COMPLETE") {
          if (currentState == STATE_CHARGING) {
            Serial.println(F("[State Transition] CHARGING -> COMPLETE (MOSFET OFF)"));
            applyHardwareOutputs(STATE_COMPLETE);
          } else {
            Serial.println(F("[Command Rejected] Session can only be completed from CHARGING state."));
          }
        }
        else if (inputCommandBuffer == "RESTORE") {
          if (currentState == STATE_FAULT || currentState == STATE_COMPLETE) {
            Serial.println(F("[State Transition] RESTORING station -> AVAILABLE"));
            accumulatedEnergyWh = 0.0f;
            applyHardwareOutputs(STATE_AVAILABLE);
          } else {
            Serial.println(F("[State Notice] Station is already active / operational."));
          }
        }
        else if (inputCommandBuffer == "STATUS") {
          printStatus();
        }
        else {
          Serial.println(F("[Error] Unknown command. Valid options: START, STOP, COMPLETE, RESTORE, STATUS"));
        }

        inputCommandBuffer = "";
      }
    } else {
      inputCommandBuffer += c;
    }
  }
}

// -----------------------------------------------------------------------------
// Telemetry HTTP Transmission (FastAPI Backend Ingestion)
// -----------------------------------------------------------------------------
void sendTelemetryToBackend() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("[Backend Stream] Wi-Fi not connected. Skipping remote telemetry post."));
    return;
  }

  HTTPClient http;
  String endpoint = String(API_BASE_URL) + "/station/telemetry/ingest";

  // Compute nominal electrical values based on state
  float volt = (currentState == STATE_CHARGING) ? NOMINAL_VOLTAGE_V : 0.0f;
  float curr = (currentState == STATE_CHARGING) ? NOMINAL_CURRENT_A : 0.0f;
  float pwr  = (currentState == STATE_CHARGING) ? NOMINAL_POWER_W   : 0.0f;
  bool isFault = (currentState == STATE_FAULT);
  bool relayClosed = (currentState == STATE_CHARGING);

  // Construct JSON payload
  char payload[384];
  if (isFault) {
    snprintf(payload, sizeof(payload),
      "{\"station_id\":\"%s\",\"source\":\"HARDWARE\",\"state\":\"%s\","
      "\"voltage_v\":%.1f,\"current_a\":%.1f,\"power_w\":%.1f,\"energy_wh\":%.2f,"
      "\"fault\":true,\"fault_code\":\"%s\",\"controller_connected\":true,"
      "\"telemetry_connected\":false,\"telemetry_quality\":\"NOMINAL\",\"relay_closed\":false}",
      STATION_ID,
      getBackendStateString(currentState),
      volt, curr, pwr, accumulatedEnergyWh,
      faultReason.c_str()
    );
  } else {
    snprintf(payload, sizeof(payload),
      "{\"station_id\":\"%s\",\"source\":\"HARDWARE\",\"state\":\"%s\","
      "\"voltage_v\":%.1f,\"current_a\":%.1f,\"power_w\":%.1f,\"energy_wh\":%.2f,"
      "\"fault\":false,\"fault_code\":null,\"controller_connected\":true,"
      "\"telemetry_connected\":false,\"telemetry_quality\":\"NOMINAL\",\"relay_closed\":%s}",
      STATION_ID,
      getBackendStateString(currentState),
      volt, curr, pwr, accumulatedEnergyWh,
      relayClosed ? "true" : "false"
    );
  }

  http.begin(endpoint);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(2500); // 2.5s timeout to keep local state machine responsive

  int httpResponseCode = http.POST(payload);

  if (httpResponseCode == 200) {
    Serial.print(F("[Backend Stream] POST 200 OK -> State: "));
    Serial.print(getStateString(currentState));
    Serial.print(F(" | MOSFET: "));
    Serial.print(relayClosed ? F("ON") : F("OFF"));
    Serial.print(F(" | Quality: NOMINAL | Energy: "));
    Serial.print(accumulatedEnergyWh, 1);
    Serial.println(F(" Wh"));
  } else if (httpResponseCode > 0) {
    Serial.print(F("[Backend Warning] HTTP Code "));
    Serial.print(httpResponseCode);
    Serial.println(F(". Local station state preserved."));
  } else {
    Serial.print(F("[Backend Error] HTTP POST failed: "));
    Serial.print(http.errorToString(httpResponseCode).c_str());
    Serial.println(F(". Local operation continuing."));
  }

  http.end();
}

// -----------------------------------------------------------------------------
// Detailed Status Reporting
// -----------------------------------------------------------------------------
void printStatus() {
  Serial.println(F("\n--- [VoltGuide Station Status] ---"));
  Serial.print(F("  Station ID        : ")); Serial.println(STATION_ID);
  Serial.print(F("  Hardware State    : ")); Serial.println(getStateString(currentState));
  Serial.print(F("  MOSFET Output     : ")); Serial.println((currentState == STATE_CHARGING) ? F("HIGH (CLOSED / ENERGIZED)") : F("LOW (OPEN / ISOLATED)"));
  Serial.print(F("  Fault Flag        : ")); Serial.println((currentState == STATE_FAULT) ? F("ACTIVE (MANUAL_FAULT)") : F("CLEAR"));
  Serial.print(F("  Telemetry Quality : NOMINAL (Estimated load baseline; no INA219)")); Serial.println();
  Serial.print(F("  Nominal Voltage   : ")); Serial.print((currentState == STATE_CHARGING) ? NOMINAL_VOLTAGE_V : 0.0f, 1); Serial.println(F(" V"));
  Serial.print(F("  Nominal Current   : ")); Serial.print((currentState == STATE_CHARGING) ? NOMINAL_CURRENT_A : 0.0f, 1); Serial.println(F(" A"));
  Serial.print(F("  Nominal Power     : ")); Serial.print((currentState == STATE_CHARGING) ? (NOMINAL_POWER_W / 1000.0f) : 0.0f, 2); Serial.println(F(" kW"));
  Serial.print(F("  Estimated Energy  : ")); Serial.print(accumulatedEnergyWh, 2); Serial.println(F(" Wh"));
  Serial.print(F("  Wi-Fi Status      : ")); Serial.println((WiFi.status() == WL_CONNECTED) ? String("CONNECTED (" + WiFi.localIP().toString() + ")") : String("DISCONNECTED"));
  Serial.print(F("  Target Endpoint   : ")); Serial.print(API_BASE_URL); Serial.println(F("/station/telemetry/ingest"));
  Serial.println(F("----------------------------------\n"));
}

// -----------------------------------------------------------------------------
// State Helpers
// -----------------------------------------------------------------------------
const char* getStateString(StationState state) {
  switch (state) {
    case STATE_AVAILABLE: return "AVAILABLE";
    case STATE_CHARGING:  return "CHARGING";
    case STATE_COMPLETE:  return "COMPLETE";
    case STATE_FAULT:     return "FAULT";
    default:              return "UNKNOWN";
  }
}

const char* getBackendStateString(StationState state) {
  switch (state) {
    case STATE_AVAILABLE: return "AVAILABLE";
    case STATE_CHARGING:  return "CHARGING";
    case STATE_COMPLETE:  return "COMPLETE";
    case STATE_FAULT:     return "FAULTED";
    default:              return "AVAILABLE";
  }
}
