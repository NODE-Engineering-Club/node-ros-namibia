// ============================================================================
//  Asket EC  -  Pico 2 (RP2350) Low-Level Controller   [Motherboard v2]
//  Core: Earle Philhower arduino-pico
//
//  Based on the Foxglove controller from the old motherboard. Changes:
//    - RC: CRSF (RadioMaster RP4TD, ExpressLRS) on UART1 instead of SBUS
//    - Jetson link: UART0 (GP0/GP1) instead of USB. USB = debug mirror.
//    - Two-stage power-up: precharge relay -> bus settled -> main contactor
//    - Jetson power cycle via regulator EN (RC switch only, never automatic)
//    - Jetson UART goes high-Z while the Jetson is unpowered (no back-feed)
//    - SHT4x enclosure temperature / humidity (software I2C, see below),
//      leak sensor, 12 V rail monitor
//    - Bus-sag throttle fallback (e.g. one battery pack tripped)
//    - Hardware watchdog
//
//  Modes (CH_MODE, 3-pos):  STOPPED (e-stop) / MANUAL / AUTONOMY-PERMITTED
//  Arm: CH_ARM = -1 -> armed whenever the mode switch is in MANUAL or AUTO
//       (no separate arm switch). Set CH_ARM to a channel for two-step arming.
//  Non-blocking loop: no delay() after setup.
// ============================================================================
#include <Servo.h>
#include "hardware/gpio.h"

// ---------------------------------------------------------------------------
//  PIN MAP  (Motherboard v2)
// ---------------------------------------------------------------------------
#define JETSON_TX_PIN      0    // UART0 TX -> Jetson header pin 10 (RX)
#define JETSON_RX_PIN      1    // UART0 RX <- Jetson header pin 8  (TX)
#define LED_JETSON_PIN     2    // on = Jetson heartbeat live
#define LED_RC_PIN         3    // on = RC link live
#define CRSF_TX_PIN        4    // UART1 TX -> RP4TD RX
#define CRSF_RX_PIN        5    // UART1 RX <- RP4TD TX
// SHT4x: SDA/SCL are swapped on the v2 board relative to the I2C1 pin
// functions, so the sensor runs on a software I2C (open-drain bit-bang,
// using the board pull-ups R30/R31). Tested with sht_bitbang_test.
#define SHT_SDA_PIN        7
#define SHT_SCL_PIN        6
#define GREEN_LIGHT_PIN    10
#define YELLOW_LIGHT_PIN   11
#define RED_LIGHT_PIN      12
#define BUZZER_PIN         13
#define ESC2_PIN           15
#define ESC1_PIN           16
#define LED_STATUS_PIN     17   // loop heartbeat blink
#define LEAK_PIN           18   // Blue Robotics SOS: HIGH = leak
#define JETSON_PG_PIN      19   // regulator PG (see USE_JETSON_PG)
#define JETSON_OFF_PIN     20   // HIGH -> AO3400A pulls EN low -> Jetson regulator OFF
#define MAIN_RELAY_PIN     21   // HIGH = main contactor closed (ESC bus live)
#define PRECHG_RELAY_PIN   22   // HIGH = precharge relay closed (47 R into ESC bus)
#define VBUS_ADC_PIN       27   // ESC bus, divider 20k / 3.3k
#define V12_ADC_PIN        28   // 12 V rail, divider 20k / 3.3k

// Which ESC is which side. Swap here if the boat turns the wrong way.
#define THRUSTER_LEFT_PIN  ESC1_PIN
#define THRUSTER_RIGHT_PIN ESC2_PIN

// ---------------------------------------------------------------------------
//  FEATURE SWITCHES
// ---------------------------------------------------------------------------
// PG: verified on the bench (toggles with the EN command). 1 = the Jetson UART
// is only driven while PG reports the Jetson regulator in regulation.
#define USE_JETSON_PG          1
// 1 = losing the RC link e-stops even in AUTONOMOUS (safe default).
// 0 = an autonomous mission continues without RC (Jetson keeps driving).
#define RC_LOSS_ESTOP_IN_AUTO  1
// 1 = a confirmed leak e-stops the thrusters. 0 = alarm only (lets you drive home).
#define LEAK_ESTOP             0
// Bench-only: drive from serial with NO RC. Removes RC e-stop authority.
#define BENCH_NO_RC_OVERRIDE   0

// ---------------------------------------------------------------------------
//  ANALOG (both dividers 20k top / 3.3k bottom)
// ---------------------------------------------------------------------------
const float ADC_REF_V     = 3.3f;
const int   ADC_MAX       = 4095;
const float DIV_RATIO     = 3.3f / (20.0f + 3.3f);   // 0.1416 -> 16.8 V = 2.38 V at pin
const float VBUS_CAL      = 0.983f;  // calibrated: 16.99 V meter / 17.27 V read
const float V12_CAL       = 0.980f;  // calibrated: 11.98 V meter / 12.22 V read

// ---------------------------------------------------------------------------
//  POWER-UP / BUS
// ---------------------------------------------------------------------------
// The ESCs power up during precharge and play their start tones: each tone
// pulls current through the 47 R and makes the bus jump by several volts.
// So instead of waiting for a perfectly settled bus, close the contactor as
// soon as the bus is near its peak (= close to pack voltage), after >= 5 tau.
const unsigned long PRECHG_MIN_MS     = 500;   // >= 5 tau (47 R x ~2000 uF = 94 ms)
const unsigned long PRECHG_TIMEOUT_MS = 3000;  // never got near the peak -> fault
const float         PRECHG_MIN_V      = 11.5f; // bus must at least reach this
const float         PRECHG_PEAK_FRAC  = 0.90f; // close when bus >= 90 % of the peak seen
const unsigned long PRECHG_OVERLAP_MS = 50;    // keep precharge closed after contactor closes
const unsigned long ESC_ARM_DELAY_MS  = 2000;  // neutral hold after contactor closes
const unsigned long BUS_GRACE_MS      = 300;   // ignore bus-loss check right after closing
const float         BUS_LOST_V        = 10.0f; // below this while ON = power lost
const unsigned long BUS_LOST_DEBOUNCE_MS = 100;
const float         BUS_LIVE_WARN_V   = 8.0f;  // bus still above this long after opening
const unsigned long BUS_LIVE_WARN_MS  = 5000;  //   -> contactor welded / stuck warning

// ---------------------------------------------------------------------------
//  THRUSTER LIMIT (per thruster, T200) - fixed PWM ceilings, read off the
//  Blue Robotics performance chart (bollard, i.e. worst case):
//    normal: 1760 us -> 8.7 A @ 16 V (~2x the thrust of the old U2 Mini)
//    sag:    1664 us -> < 3 A        (about the old motors' power)
//  Reverse is mirrored around 1500 (1240 / 1336 us).
//  Budget normal: 2 x ~9 A + ~10 A electronics = ~28 A over 3 packs (~9 A each).
// ---------------------------------------------------------------------------
const int PWM_LIMIT_US     = 260;   // 1500 + 260 = 1760 us
const int PWM_LIMIT_SAG_US = 164;   // 1500 + 164 = 1664 us
const float MAX_AUTHORITY  = 1.00f;   // extra command scale on top of the limit
const float SAG_ENTER_V      = 13.0f;  // bus below this (under load) ...
const unsigned long SAG_ENTER_MS = 300;//   ... for this long -> reduced current limit
const float SAG_EXIT_V       = 13.6f;  // bus above this ...
const unsigned long SAG_EXIT_MS  = 2000;// ... for this long -> normal limit again
const unsigned long SAG_BLINK_MS = 250;// active signal light blinks while in sag

// ---------------------------------------------------------------------------
//  PWM (ESC) - bidirectional
// ---------------------------------------------------------------------------
// Blue Robotics ESC: 1100 = full reverse, 1500 = stop (deadband +/-25), 1900 = full forward
const int PWM_MIN_US     = 1100;
const int PWM_NEUTRAL_US = 1500;
const int PWM_MAX_US     = 1900;
const int PWM_DEADBAND_US = 25;
const int PWM_SPAN_US    = 400;

// ---------------------------------------------------------------------------
//  RC (CRSF) - channel range is the same 11-bit scale as SBUS
// ---------------------------------------------------------------------------
const uint16_t RC_MIN      = 174;
const uint16_t RC_MID      = 992;
const uint16_t RC_MAX      = 1811;
const uint16_t RC_DEADBAND = 30;
const unsigned long RC_TIMEOUT_MS = 500;   // no RC frame this long = link lost

// Channel assignment (0-indexed), measured on the MT12:
//   CH1 steering 174/992/1811, CH2 throttle 174/992/1811,
//   CH3 mode 174 = stopped / 992 = manual / 1811 = auto,
//   CH8 Jetson reboot 191 released / 1792 pressed
const int CH_YAW          = 0;   // CH1
const int CH_THROTTLE     = 1;   // CH2
const int CH_MODE         = 2;   // CH3
const int CH_ARM          = -1;  // none: mode switch arms (see top of file)
const int CH_JETSON_CYCLE = 7;   // CH8: hold for JETSON_CYCLE_HOLD_MS to power-cycle
// Arming only with throttle and steering centred: the boat never starts
// moving because a stick was off-centre when the mode switch was flipped.
#define     ARM_REQUIRES_NEUTRAL 1
const float ARM_NEUTRAL_MAX   = 0.10f;   // |stick| below this counts as centred

const int ARM_THRESHOLD  = 1400; // CH_ARM above = armed
const int MODE_LOW_MAX   = 700;  // CH_MODE below = ESTOP
const int MODE_MID_MAX   = 1400; // below = MANUAL, above = autonomy permitted
const int CYCLE_THRESHOLD = 1400;

// ---------------------------------------------------------------------------
//  MIXING
// ---------------------------------------------------------------------------
const float YAW_GAIN   = 0.7f;
const int   YAW_INVERT = +1;
const int   THR_INVERT = +1;
const float MANUAL_AUTHORITY = 1.0f;
#define     PIVOT_AT_MAX_YAW 1
const float PIVOT_THRESHOLD  = 0.9f;

// ---------------------------------------------------------------------------
//  JETSON
// ---------------------------------------------------------------------------
const unsigned long JETSON_BAUD             = 115200;
const unsigned long JETSON_CYCLE_HOLD_MS    = 3000;   // RC switch hold time
const unsigned long JETSON_OFF_MS           = 10000;  // power-off duration during a cycle
const unsigned long SERIAL_FAILSAFE_TIMEOUT_MS = 500; // auto: no motor cmd -> neutral
const unsigned long HEARTBEAT_TIMEOUT_MS    = 600;

// ---------------------------------------------------------------------------
//  ENCLOSURE / LEAK
// ---------------------------------------------------------------------------
const uint8_t SHT4X_ADDR       = 0x44;
const unsigned long SHT_PERIOD_MS = 2000;
const float   ENCL_TEMP_WARN_C = 60.0f;
const float   ENCL_RH_WARN     = 85.0f;
const unsigned long LEAK_DEBOUNCE_MS = 1000;
const unsigned long LEAK_BEEP_PERIOD_MS = 5000;
const float   V12_WARN_V       = 11.0f;

const unsigned long STATUS_PERIOD_MS = 250;

// ============================================================================
//  STATE
// ============================================================================
enum OperationMode { MODE_ESTOP = 1, MODE_MANUAL = 2, MODE_AUTONOMOUS = 3 };
OperationMode current_mode = MODE_ESTOP;
bool armed = false;
bool estop_latched = false;

enum PowerState { PWR_OFF = 0, PWR_PRECHARGE = 1, PWR_CLOSING = 2, PWR_ON = 3 };
PowerState pwr = PWR_OFF;
unsigned long pwr_t0 = 0;           // precharge start
unsigned long main_on_ms = 0;       // contactor closed at
unsigned long pwr_off_ms = 0;       // contactor opened at
float vbus_peak = 0;
bool bus_low_pending = false; unsigned long bus_low_since = 0;
bool bus_stuck_warn = false;

float vbus = 0, v12 = 0;
bool sag_active = false; unsigned long sag_since = 0; bool sag_pending = false;

// RC
const int NUM_CHANNELS = 16;
uint16_t rc_ch[NUM_CHANNELS];
unsigned long last_rc_ms = 0;
uint8_t  rc_lq = 0;           // uplink link quality, %
int8_t   rc_rssi = 0;         // uplink RSSI, dBm (negative)
unsigned long rc_frames = 0, rc_bad = 0, rc_max_gap = 0;
uint8_t  crsf_buf[64];
uint8_t  crsf_idx = 0;

// Jetson
bool jetson_off_cmd = false;
unsigned long jetson_off_since = 0;
bool jetson_uart_on = false;
bool cycle_armed_edge = true;       // switch must return low before next cycle
bool cycle_pending = false; unsigned long cycle_hold_since = 0;
unsigned long last_heartbeat_ms = 0;
unsigned long last_serial_cmd_ms = 0;
bool serial_wants_auto = false;

// Command line buffers (Jetson UART and USB debug)
const size_t LINE_MAX = 64;
struct LineBuf { char b[LINE_MAX]; size_t n = 0; };
LineBuf jet_line, usb_line;

// Enclosure / leak
float encl_t = NAN, encl_rh = NAN;
bool sht_ok = false;
uint8_t sht_state = 0; unsigned long sht_t = 0;
bool leak = false; bool leak_pending = false; unsigned long leak_since = 0;
unsigned long last_leak_beep = 0;

Servo thruster_left, thruster_right;

// ---------------------------------------------------------------------------
//  Output to both the Jetson UART (when powered) and USB (debug)
// ---------------------------------------------------------------------------
class DualOut : public Print {
public:
  size_t write(uint8_t c) override {
    if (jetson_uart_on) Serial1.write(c);
    if (Serial) Serial.write(c);
    return 1;
  }
  size_t write(const uint8_t *buf, size_t len) override {
    if (jetson_uart_on) Serial1.write(buf, len);
    if (Serial) Serial.write(buf, len);
    return len;
  }
} out;

// ---------------------------------------------------------------------------
//  Non-blocking beeper (from the old firmware)
// ---------------------------------------------------------------------------
struct Beeper {
  bool active = false; int remaining = 0;
  unsigned long onMs = 0, offMs = 0, phaseStart = 0; bool isOn = false;
} beeper;

void startBeeps(int count, unsigned long onMs, unsigned long offMs) {
  if (count <= 0) { beeper.active = false; digitalWrite(BUZZER_PIN, LOW); return; }
  beeper.active = true; beeper.remaining = count;
  beeper.onMs = onMs; beeper.offMs = offMs;
  beeper.isOn = true; beeper.phaseStart = millis();
  digitalWrite(BUZZER_PIN, HIGH);
}

void updateBeeper() {
  if (!beeper.active) return;
  unsigned long now = millis();
  if (beeper.isOn) {
    if (now - beeper.phaseStart >= beeper.onMs) {
      digitalWrite(BUZZER_PIN, LOW);
      beeper.isOn = false; beeper.phaseStart = now;
      if (--beeper.remaining <= 0) beeper.active = false;
    }
  } else if (now - beeper.phaseStart >= beeper.offMs) {
    digitalWrite(BUZZER_PIN, HIGH);
    beeper.isOn = true; beeper.phaseStart = now;
  }
}

// ============================================================================
//  HELPERS
// ============================================================================
float read_divider(int pin, float cal) {
  uint32_t acc = 0;
  for (int i = 0; i < 8; i++) acc += analogRead(pin);
  float v_pin = (acc / 8.0f) * ADC_REF_V / (float)ADC_MAX;
  return v_pin / DIV_RATIO * cal;
}

bool rc_link_ok() {
#if BENCH_NO_RC_OVERRIDE
  return true;
#else
  return (millis() - last_rc_ms) <= RC_TIMEOUT_MS;
#endif
}

bool serial_link_live() { return (millis() - last_heartbeat_ms) <= HEARTBEAT_TIMEOUT_MS; }

bool jetson_pg() {
#if USE_JETSON_PG
  return digitalRead(JETSON_PG_PIN) == HIGH;
#else
  return true;   // PG not usable on this hardware revision
#endif
}

float rc_norm(uint16_t v) {
  int c = (int)v - (int)RC_MID;
  if (abs(c) < (int)RC_DEADBAND) return 0.0f;
  float n;
  if (c > 0) n = (float)(c - RC_DEADBAND) / (float)((int)RC_MAX - (int)RC_MID - (int)RC_DEADBAND);
  else       n = (float)(c + RC_DEADBAND) / (float)((int)RC_MID - (int)RC_MIN - (int)RC_DEADBAND);
  return constrain(n, -1.0f, 1.0f);
}

// Largest PWM offset from 1500 (us) allowed right now
int pwm_offset_limit() { return sag_active ? PWM_LIMIT_SAG_US : PWM_LIMIT_US; }

// command -1..+1 -> pulse, linear between the deadband edge and the PWM limit
int cmd_to_pulse(float c, int off_max) {
  c = constrain(c * MAX_AUTHORITY, -1.0f, 1.0f);
  if (fabsf(c) < 0.01f) return PWM_NEUTRAL_US;
  int off = PWM_DEADBAND_US + (int)(fabsf(c) * (off_max - PWM_DEADBAND_US));
  return constrain(PWM_NEUTRAL_US + (c > 0 ? off : -off), PWM_MIN_US, PWM_MAX_US);
}

int last_pulse_l = PWM_NEUTRAL_US, last_pulse_r = PWM_NEUTRAL_US;
void set_both(int us) {
  last_pulse_l = last_pulse_r = us;
  thruster_left.writeMicroseconds(us);
  thruster_right.writeMicroseconds(us);
}


// left/right as -1..+1; full stick = the current-limited maximum
void write_thrusters_norm(float l, float r) {
  int off_max = pwm_offset_limit();
  last_pulse_l = cmd_to_pulse(l, off_max);
  last_pulse_r = cmd_to_pulse(r, off_max);
  thruster_left.writeMicroseconds(last_pulse_l);
  thruster_right.writeMicroseconds(last_pulse_r);
}

bool thrust_allowed() {
  return armed && pwr == PWR_ON && (millis() - main_on_ms >= ESC_ARM_DELAY_MS);
}

// The active light is remembered; update_lights() drives it (steady, or
// blinking while the bus is in sag = low battery / tripped pack).
int active_light = RED_LIGHT_PIN;
void set_light(OperationMode mode, bool live) {
  if (!live || mode == MODE_ESTOP) active_light = RED_LIGHT_PIN;
  else if (mode == MODE_MANUAL)    active_light = YELLOW_LIGHT_PIN;
  else                             active_light = GREEN_LIGHT_PIN;
}
void update_lights() {
  bool on = true;
  if (sag_active) on = ((millis() / SAG_BLINK_MS) & 1) == 0;
  digitalWrite(RED_LIGHT_PIN,    (active_light == RED_LIGHT_PIN    && on) ? HIGH : LOW);
  digitalWrite(YELLOW_LIGHT_PIN, (active_light == YELLOW_LIGHT_PIN && on) ? HIGH : LOW);
  digitalWrite(GREEN_LIGHT_PIN,  (active_light == GREEN_LIGHT_PIN  && on) ? HIGH : LOW);
}

// ============================================================================
//  POWER STAGE: precharge -> contactor
// ============================================================================
void relays_off() {
  digitalWrite(MAIN_RELAY_PIN, LOW);
  digitalWrite(PRECHG_RELAY_PIN, LOW);
  if (pwr != PWR_OFF) pwr_off_ms = millis();
  pwr = PWR_OFF;
  bus_low_pending = false;
}

void trigger_estop(const char *reason);

void power_request(bool on) {
  if (!on) { relays_off(); return; }
  if (pwr != PWR_OFF) return;
  digitalWrite(PRECHG_RELAY_PIN, HIGH);
  pwr = PWR_PRECHARGE;
  pwr_t0 = millis();
  vbus_peak = 0;
}

void power_update() {
  unsigned long now = millis();
  switch (pwr) {
    case PWR_PRECHARGE: {
      if (vbus > vbus_peak) vbus_peak = vbus;
      if (now - pwr_t0 >= PRECHG_MIN_MS && vbus >= PRECHG_MIN_V &&
          vbus >= PRECHG_PEAK_FRAC * vbus_peak) {
        digitalWrite(MAIN_RELAY_PIN, HIGH);
        main_on_ms = now;
        pwr = PWR_CLOSING;
      } else if (now - pwr_t0 > PRECHG_TIMEOUT_MS) {
        char buf[64];
        snprintf(buf, sizeof(buf), "precharge failed (vbus=%.1f V, peak=%.1f V)", vbus, vbus_peak);
        trigger_estop(buf);
      }
      break;
    }
    case PWR_CLOSING:
      if (now - main_on_ms >= PRECHG_OVERLAP_MS) {
        digitalWrite(PRECHG_RELAY_PIN, LOW);
        pwr = PWR_ON;
      }
      break;
    case PWR_ON:
      // Bus lost while the contactor is commanded closed: physical e-stop or fault
      if (now - main_on_ms >= BUS_GRACE_MS) {
        if (vbus < BUS_LOST_V) {
          if (!bus_low_pending) { bus_low_pending = true; bus_low_since = now; }
          else if (now - bus_low_since >= BUS_LOST_DEBOUNCE_MS)
            trigger_estop("motor power lost (physical e-stop or power fault)");
        } else bus_low_pending = false;
      }
      // Bus sag -> reduced authority
      if (!sag_active) {
        if (vbus < SAG_ENTER_V) {
          if (!sag_pending) { sag_pending = true; sag_since = now; }
          else if (now - sag_since >= SAG_ENTER_MS) {
            sag_active = true; sag_pending = false;
            out.println(F("[WARN] bus sag -> thruster current limit reduced"));
          }
        } else sag_pending = false;
      } else {
        if (vbus > SAG_EXIT_V) {
          if (!sag_pending) { sag_pending = true; sag_since = now; }
          else if (now - sag_since >= SAG_EXIT_MS) {
            sag_active = false; sag_pending = false;
            out.println(F("[INFO] bus recovered -> normal thruster current limit"));
          }
        } else sag_pending = false;
      }
      break;
    case PWR_OFF:
      sag_active = false; sag_pending = false;
      // Bus should decay after opening. Still high long after -> stuck contactor?
      bus_stuck_warn = (now - pwr_off_ms > BUS_LIVE_WARN_MS) && (vbus > BUS_LIVE_WARN_V);
      break;
  }
}

// ============================================================================
//  CRSF
// ============================================================================
uint8_t crc8_d5(const uint8_t *p, uint8_t len) {
  uint8_t crc = 0;
  while (len--) {
    crc ^= *p++;
    for (int i = 0; i < 8; i++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0xD5) : (uint8_t)(crc << 1);
  }
  return crc;
}

void crsf_handle_frame() {
  // crsf_buf: [addr][len][type][payload ...][crc], len = type + payload + crc
  uint8_t len  = crsf_buf[1];
  uint8_t type = crsf_buf[2];
  if (crc8_d5(&crsf_buf[2], len - 1) != crsf_buf[len + 1]) { rc_bad++; return; }
  const uint8_t *p = &crsf_buf[3];
  if (type == 0x16 && len == 24) {            // RC channels packed: 16 x 11 bit
    uint32_t bits = 0; int nbits = 0, ch = 0;
    for (int i = 0; i < 22 && ch < NUM_CHANNELS; i++) {
      bits |= (uint32_t)p[i] << nbits; nbits += 8;
      while (nbits >= 11 && ch < NUM_CHANNELS) {
        rc_ch[ch++] = bits & 0x7FF; bits >>= 11; nbits -= 11;
      }
    }
    unsigned long now = millis();
    unsigned long gap = now - last_rc_ms;
    if (armed && gap > rc_max_gap) rc_max_gap = gap;
    last_rc_ms = now;
    rc_frames++;
  } else if (type == 0x14 && len >= 11) {     // link statistics
    rc_rssi = -(int8_t)p[0];
    rc_lq   = p[2];
  }
}

void handle_crsf() {
  while (Serial2.available()) {
    uint8_t b = Serial2.read();
    if (crsf_idx == 0) {
      if (b == 0xC8 || b == 0xEE || b == 0xEA) crsf_buf[crsf_idx++] = b;
    } else if (crsf_idx == 1) {
      if (b < 2 || b > 62) { crsf_idx = 0; continue; }   // invalid length -> resync
      crsf_buf[crsf_idx++] = b;
    } else {
      crsf_buf[crsf_idx++] = b;
      if (crsf_idx >= crsf_buf[1] + 2) { crsf_handle_frame(); crsf_idx = 0; }
    }
  }
}

// ============================================================================
//  STATE MACHINE  (zones as on the old board)
// ============================================================================
void apply_transition(OperationMode new_mode, bool want_armed) {
  set_both(PWM_NEUTRAL_US);
  current_mode = new_mode;
  armed = want_armed;
  if (current_mode == MODE_ESTOP) {
    power_request(false);
    set_light(MODE_ESTOP, false);
    startBeeps(1, 1000, 0);
    out.println(F(">>> MODE: E-STOP"));
    return;
  }
  if (armed) {
    rc_max_gap = 0;
    power_request(true);
    set_light(current_mode, true);
    if (current_mode == MODE_MANUAL) startBeeps(2, 200, 200);
    else                             startBeeps(3, 150, 150);
    out.print(F(">>> ARMED: "));
    out.println(current_mode == MODE_MANUAL ? F("MANUAL") : F("AUTONOMOUS"));
  } else {
    power_request(false);
    set_light(current_mode, false);
    out.print(F(">>> DISARMED ("));
    out.print(current_mode == MODE_MANUAL ? F("manual") : F("auto"));
    out.println(F(")"));
  }
}

void update_state() {
  OperationMode new_mode;
  bool arm_high;
  bool sticks_ok = true;   // neutral-stick interlock result
#if BENCH_NO_RC_OVERRIDE
  new_mode = serial_wants_auto ? MODE_AUTONOMOUS : MODE_MANUAL;
  arm_high = true;
#else
  if (!rc_link_ok()) {
    // Optionally let an autonomous mission continue without RC
    if (!RC_LOSS_ESTOP_IN_AUTO && armed && current_mode == MODE_AUTONOMOUS &&
        serial_wants_auto && serial_link_live()) return;
    new_mode = MODE_ESTOP;
    arm_high = false;
  } else {
    uint16_t mode_ch = rc_ch[CH_MODE];
    if (mode_ch < MODE_LOW_MAX)       new_mode = MODE_ESTOP;
    else if (mode_ch < MODE_MID_MAX)  new_mode = MODE_MANUAL;
    else new_mode = (serial_wants_auto && serial_link_live()) ? MODE_AUTONOMOUS : MODE_MANUAL;
    arm_high = (CH_ARM < 0) ? true : (rc_ch[CH_ARM] > ARM_THRESHOLD);
#if ARM_REQUIRES_NEUTRAL
    // Only gates the transition INTO armed; once armed, sticks are free.
    if (!armed && arm_high && new_mode != MODE_ESTOP) {
      sticks_ok = fabsf(rc_norm(rc_ch[CH_THROTTLE])) < ARM_NEUTRAL_MAX &&
                  fabsf(rc_norm(rc_ch[CH_YAW]))      < ARM_NEUTRAL_MAX;
      if (!sticks_ok) {
        static unsigned long last_msg = 0;
        if (millis() - last_msg > 2000) { last_msg = millis(); out.println(F("[INFO] arming blocked: centre throttle and steering")); }
      }
    }
#endif
  }
#endif
  bool want_armed = arm_high && (new_mode != MODE_ESTOP);
  // Latch is cleared only by the operator: arm switch off, or mode -> STOPPED.
  // (Not by the stick interlock.)
  if (!arm_high || new_mode == MODE_ESTOP) estop_latched = false;
  if (estop_latched || !sticks_ok) want_armed = false;
  if (new_mode != current_mode || want_armed != armed) apply_transition(new_mode, want_armed);
}

void trigger_estop(const char *reason) {
  armed = false;
  estop_latched = true;
  serial_wants_auto = false;
  power_request(false);
  set_both(PWM_NEUTRAL_US);
  set_light(MODE_ESTOP, false);
  startBeeps(1, 1000, 0);
  out.print(F("[E-STOP] "));
  out.println(reason);
}

// ============================================================================
//  MOTORS
// ============================================================================
void update_motors() {
  if (!thrust_allowed()) { set_both(PWM_NEUTRAL_US); return; }
  if (current_mode == MODE_MANUAL) {
    float thr     = rc_norm(rc_ch[CH_THROTTLE]) * THR_INVERT;
    float yaw_raw = rc_norm(rc_ch[CH_YAW]) * YAW_INVERT;
    float yaw     = yaw_raw * YAW_GAIN;
    float left, right;
#if PIVOT_AT_MAX_YAW
    if (fabsf(yaw_raw) >= PIVOT_THRESHOLD) {
      left = (yaw_raw > 0) ? 1.0f : -1.0f;
      right = -left;
    } else
#endif
    {
      left = thr + yaw; right = thr - yaw;
      float m = max(fabsf(left), fabsf(right));
      if (m > 1.0f) { left /= m; right /= m; }
    }
    write_thrusters_norm(left * MANUAL_AUTHORITY, right * MANUAL_AUTHORITY);
  } else if (current_mode == MODE_AUTONOMOUS) {
    if (millis() - last_serial_cmd_ms > SERIAL_FAILSAFE_TIMEOUT_MS) set_both(PWM_NEUTRAL_US);
    // otherwise thrust is written by the motor command handler
  }
}

// ============================================================================
//  JETSON: power, UART gating, commands
// ============================================================================
void jetson_uart_set(bool on) {
  if (on == jetson_uart_on) return;
  if (on) {
    Serial1.setTX(JETSON_TX_PIN);
    Serial1.setRX(JETSON_RX_PIN);
    Serial1.begin(JETSON_BAUD);
    gpio_set_drive_strength(JETSON_TX_PIN, GPIO_DRIVE_STRENGTH_2MA);
    jetson_uart_on = true;
  } else {
    jetson_uart_on = false;
    Serial1.end();
    pinMode(JETSON_TX_PIN, INPUT);   // high-Z: no current into an unpowered Jetson
    pinMode(JETSON_RX_PIN, INPUT);
  }
}

void jetson_power_update() {
  unsigned long now = millis();
  // RC-commanded power cycle: hold the switch HIGH for JETSON_CYCLE_HOLD_MS.
  // Never automatic. Not allowed while the Jetson is driving (AUTONOMOUS).
  bool sw = rc_link_ok() && rc_ch[CH_JETSON_CYCLE] > CYCLE_THRESHOLD;
  if (!sw) { cycle_armed_edge = true; cycle_pending = false; }
  else if (cycle_armed_edge && !jetson_off_cmd && current_mode != MODE_AUTONOMOUS) {
    if (!cycle_pending) { cycle_pending = true; cycle_hold_since = now; }
    else if (now - cycle_hold_since >= JETSON_CYCLE_HOLD_MS) {
      cycle_pending = false;
      cycle_armed_edge = false;          // switch must go low before the next cycle
      jetson_off_cmd = true;
      jetson_off_since = now;
      serial_wants_auto = false;
      digitalWrite(JETSON_OFF_PIN, HIGH);
      startBeeps(4, 100, 100);
      out.println(F("[JETSON] power cycle: OFF"));
    }
  }
  if (jetson_off_cmd && now - jetson_off_since >= JETSON_OFF_MS) {
    jetson_off_cmd = false;
    digitalWrite(JETSON_OFF_PIN, LOW);
    out.println(F("[JETSON] power cycle: ON"));
  }
  jetson_uart_set(!jetson_off_cmd && jetson_pg());
  if (!jetson_uart_on) serial_wants_auto = false;
}

bool parse_motor_command(const char *command, float &l, float &r) {
  char cmd[LINE_MAX];
  strncpy(cmd, command, sizeof(cmd) - 1); cmd[sizeof(cmd) - 1] = '\0';
  for (char *q = cmd; *q; ++q) if (*q=='['||*q==']'||*q==','||*q==';') *q = ' ';
  float v1 = 0, v2 = 0;
  int n = sscanf(cmd, "%f %f", &v1, &v2);
  if (n < 1) return false;
  if (n == 1) v2 = v1;
  bool norm = fabsf(v1) <= 1.5f && fabsf(v2) <= 1.5f;
  if (norm) { l = v1; r = v2; }
  else {     // raw microseconds 1000..2000
    // raw pulses are treated as a fraction of the full ESC range, then
    // current-limited like every other command
    l = (constrain(v1, (float)PWM_MIN_US, (float)PWM_MAX_US) - PWM_NEUTRAL_US) / (float)PWM_SPAN_US;
    r = (constrain(v2, (float)PWM_MIN_US, (float)PWM_MAX_US) - PWM_NEUTRAL_US) / (float)PWM_SPAN_US;
  }
  return true;
}

void process_line(char *line) {
  char *p = line;
  while (*p == ' ' || *p == '\t') p++;
  size_t n = strlen(p);
  while (n > 0 && (p[n-1] == ' ' || p[n-1] == '\t')) p[--n] = '\0';
  if (n == 0) return;
  last_heartbeat_ms = millis();
  if (strcasecmp(p, "PING") == 0) return;
  if (strcasecmp(p, "MODE AUTO") == 0)   { serial_wants_auto = true;  out.println(F("[ACK] MODE AUTO requested"));   return; }
  if (strcasecmp(p, "MODE MANUAL") == 0) { serial_wants_auto = false; out.println(F("[ACK] MODE MANUAL requested")); return; }
  float l, r;
  if (parse_motor_command(p, l, r)) {
    last_serial_cmd_ms = millis();
    if (current_mode == MODE_AUTONOMOUS && thrust_allowed()) write_thrusters_norm(l, r);
  } else {
    out.print(F("Invalid cmd: ")); out.println(p);
  }
}

void read_lines(Stream &s, LineBuf &lb) {
  while (s.available()) {
    char c = (char)s.read();
    if (c == '\n' || c == '\r') {
      if (lb.n > 0) { lb.b[lb.n] = '\0'; process_line(lb.b); lb.n = 0; }
    } else if (lb.n < LINE_MAX - 1) lb.b[lb.n++] = c;
    else lb.n = 0;   // runaway line: drop, resync on newline
  }
}

// ============================================================================
//  ENCLOSURE SENSOR (SHT4x, non-blocking) + LEAK
// ============================================================================
uint8_t crc8_31(const uint8_t *d, int n) {
  uint8_t crc = 0xFF;
  for (int i = 0; i < n; i++) {
    crc ^= d[i];
    for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
  }
  return crc;
}

// ---- software I2C (open-drain: release = HIGH via pull-up, drive = LOW) ----
const int SW_I2C_HALF_US = 10;   // ~50 kHz; a full SHT transaction blocks ~1.5 ms
inline void swi_rel(int p) { pinMode(p, INPUT); }
inline void swi_low(int p) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }
inline bool swi_rd(int p)  { return digitalRead(p) == HIGH; }

bool swi_scl_high() {            // release SCL, allow clock stretching (max 2 ms)
  swi_rel(SHT_SCL_PIN);
  unsigned long t0 = micros();
  while (!swi_rd(SHT_SCL_PIN)) if (micros() - t0 > 2000) return false;
  delayMicroseconds(SW_I2C_HALF_US);
  return true;
}
void swi_start() {
  swi_rel(SHT_SDA_PIN); swi_scl_high();
  swi_low(SHT_SDA_PIN); delayMicroseconds(SW_I2C_HALF_US);
  swi_low(SHT_SCL_PIN); delayMicroseconds(SW_I2C_HALF_US);
}
void swi_stop() {
  swi_low(SHT_SDA_PIN); delayMicroseconds(SW_I2C_HALF_US);
  swi_scl_high();
  swi_rel(SHT_SDA_PIN); delayMicroseconds(SW_I2C_HALF_US);
}
bool swi_write(uint8_t b) {      // true = ACK
  for (int i = 7; i >= 0; i--) {
    if (b & (1 << i)) swi_rel(SHT_SDA_PIN); else swi_low(SHT_SDA_PIN);
    delayMicroseconds(SW_I2C_HALF_US);
    swi_scl_high();
    swi_low(SHT_SCL_PIN);
  }
  swi_rel(SHT_SDA_PIN); delayMicroseconds(SW_I2C_HALF_US);
  swi_scl_high();
  bool ack = !swi_rd(SHT_SDA_PIN);
  swi_low(SHT_SCL_PIN); delayMicroseconds(SW_I2C_HALF_US);
  return ack;
}
uint8_t swi_read(bool ack) {
  uint8_t b = 0;
  swi_rel(SHT_SDA_PIN);
  for (int i = 7; i >= 0; i--) {
    delayMicroseconds(SW_I2C_HALF_US);
    swi_scl_high();
    if (swi_rd(SHT_SDA_PIN)) b |= (1 << i);
    swi_low(SHT_SCL_PIN);
  }
  if (ack) swi_low(SHT_SDA_PIN); else swi_rel(SHT_SDA_PIN);
  delayMicroseconds(SW_I2C_HALF_US);
  swi_scl_high();
  swi_low(SHT_SCL_PIN);
  swi_rel(SHT_SDA_PIN);
  return b;
}
void swi_recover() {             // clock out a slave stuck mid-byte
  swi_rel(SHT_SDA_PIN);
  for (int i = 0; i < 9; i++) { swi_low(SHT_SCL_PIN); delayMicroseconds(SW_I2C_HALF_US); swi_scl_high(); }
  swi_stop();
}

// Non-blocking measurement: send command, come back after >= 10 ms, read.
void sht_update() {
  unsigned long now = millis();
  if (sht_state == 0) {
    if (now - sht_t < SHT_PERIOD_MS) return;
    sht_t = now;
    swi_start();
    bool ok = swi_write(SHT4X_ADDR << 1) && swi_write(0xFD);   // T + RH, high precision
    swi_stop();
    if (!ok) { sht_ok = false; swi_recover(); return; }
    sht_state = 1;
  } else {
    if (now - sht_t < 10) return;            // conversion time ~8.3 ms
    sht_state = 0; sht_t = now;
    uint8_t d[6];
    swi_start();
    if (!swi_write((SHT4X_ADDR << 1) | 1)) { swi_stop(); sht_ok = false; swi_recover(); return; }
    for (int i = 0; i < 6; i++) d[i] = swi_read(i < 5);
    swi_stop();
    if (crc8_31(d, 2) == d[2] && crc8_31(d + 3, 2) == d[5]) {
      uint16_t rt = (d[0] << 8) | d[1], rh = (d[3] << 8) | d[4];
      encl_t  = -45.0f + 175.0f * rt / 65535.0f;
      encl_rh = constrain(-6.0f + 125.0f * rh / 65535.0f, 0.0f, 100.0f);
      sht_ok = true;
    } else sht_ok = false;
  }
}

void leak_update() {
  unsigned long now = millis();
  bool raw = digitalRead(LEAK_PIN) == HIGH;
  if (!leak) {
    if (raw) {
      if (!leak_pending) { leak_pending = true; leak_since = now; }
      else if (now - leak_since >= LEAK_DEBOUNCE_MS) {
        leak = true;                         // latched until Pico reset
        out.println(F("[ALARM] LEAK DETECTED"));
#if LEAK_ESTOP
        trigger_estop("leak detected");
#endif
      }
    } else leak_pending = false;
  } else if (now - last_leak_beep >= LEAK_BEEP_PERIOD_MS && !beeper.active) {
    last_leak_beep = now;
    startBeeps(5, 80, 80);
  }
}

// ============================================================================
//  SETUP
// ============================================================================
void setup() {
  // Outputs to a safe state first
  pinMode(MAIN_RELAY_PIN, OUTPUT);   digitalWrite(MAIN_RELAY_PIN, LOW);
  pinMode(PRECHG_RELAY_PIN, OUTPUT); digitalWrite(PRECHG_RELAY_PIN, LOW);
  pinMode(JETSON_OFF_PIN, OUTPUT);   digitalWrite(JETSON_OFF_PIN, LOW);   // Jetson ON
  pinMode(RED_LIGHT_PIN, OUTPUT); pinMode(YELLOW_LIGHT_PIN, OUTPUT);
  pinMode(GREEN_LIGHT_PIN, OUTPUT); pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  pinMode(LED_JETSON_PIN, OUTPUT); pinMode(LED_RC_PIN, OUTPUT); pinMode(LED_STATUS_PIN, OUTPUT);
  pinMode(LEAK_PIN, INPUT_PULLDOWN);  // sensor drives HIGH on leak
  pinMode(JETSON_PG_PIN, INPUT);      // external pull-up on the board
  pinMode(JETSON_TX_PIN, INPUT); pinMode(JETSON_RX_PIN, INPUT);
  analogReadResolution(12);

  thruster_left.attach(THRUSTER_LEFT_PIN, PWM_MIN_US, PWM_MAX_US);
  thruster_right.attach(THRUSTER_RIGHT_PIN, PWM_MIN_US, PWM_MAX_US);
  set_both(PWM_NEUTRAL_US);

  for (int i = 0; i < NUM_CHANNELS; i++) rc_ch[i] = RC_MID;
  if (CH_ARM >= 0) rc_ch[CH_ARM] = RC_MIN;
  rc_ch[CH_MODE] = RC_MIN; rc_ch[CH_JETSON_CYCLE] = RC_MIN;

  Serial.begin(115200);                 // USB debug (does not block)

  Serial2.setTX(CRSF_TX_PIN);
  Serial2.setRX(CRSF_RX_PIN);
  Serial2.setFIFOSize(512);             // status prints must never starve CRSF RX
  Serial2.begin(420000);

  swi_rel(SHT_SDA_PIN);                 // software I2C lines released (pull-ups)
  swi_rel(SHT_SCL_PIN);
  swi_recover();

  // Boot blink (only blocking part, before the watchdog starts)
  const int pins[3] = { RED_LIGHT_PIN, YELLOW_LIGHT_PIN, GREEN_LIGHT_PIN };
  for (int s = 0; s < 6; s++) {
    digitalWrite(RED_LIGHT_PIN, LOW); digitalWrite(YELLOW_LIGHT_PIN, LOW); digitalWrite(GREEN_LIGHT_PIN, LOW);
    digitalWrite(pins[s % 3], HIGH);
    delay(150);
  }
  set_light(MODE_ESTOP, false);

  vbus = read_divider(VBUS_ADC_PIN, VBUS_CAL);
  v12  = read_divider(V12_ADC_PIN, V12_CAL);
  last_heartbeat_ms = last_serial_cmd_ms = millis();
  pwr_off_ms = millis();

  jetson_power_update();                // opens the Jetson UART if allowed

  out.println(F("=== Asket EC Pico Controller (motherboard v2) ==="));
  out.println(F("RC: CRSF  | CH3 LOW=STOPPED MID=MANUAL HIGH=autonomy-permitted | CH8 hold 3 s = Jetson reboot"));
  out.println(F("Serial: 'L R' or 'L,R' motors | 'MODE AUTO' | 'MODE MANUAL' | 'PING'"));

  rp2040.wdt_begin(500);                // MCU hang -> reset -> relay pins low -> bus off
}

// ============================================================================
//  LOOP
// ============================================================================
void loop() {
  rp2040.wdt_reset();

  handle_crsf();
  if (jetson_uart_on) read_lines(Serial1, jet_line);
  read_lines(Serial, usb_line);

  vbus = read_divider(VBUS_ADC_PIN, VBUS_CAL);
  v12  = read_divider(V12_ADC_PIN, V12_CAL);

  // RC link loss while armed (frames stopped). Checked BEFORE update_state so the
  // e-stop latches and is reported, instead of a silent transition to ESTOP.
  if (armed && !rc_link_ok() && (current_mode != MODE_AUTONOMOUS || RC_LOSS_ESTOP_IN_AUTO))
    trigger_estop("RC link lost");

  update_state();
  power_update();
  jetson_power_update();
  update_motors();
  sht_update();
  leak_update();
  updateBeeper();
  update_lights();

  // LEDs
  unsigned long now = millis();
  digitalWrite(LED_JETSON_PIN, serial_link_live() ? HIGH : LOW);
  digitalWrite(LED_RC_PIN, rc_link_ok() ? HIGH : LOW);
  digitalWrite(LED_STATUS_PIN, ((now / 500) & 1) ? HIGH : LOW);

  // Status line. Keys of the old firmware kept where the meaning is unchanged;
  // ch7/ch8 now carry the ARM and MODE channels, sbus fields are now rc fields.
  static unsigned long last_status = 0;
  if (now - last_status >= STATUS_PERIOD_MS) {
    last_status = now;
    out.print(F("STATE mode="));   out.print((int)current_mode);
    out.print(F(" armed="));       out.print(armed ? 1 : 0);
    out.print(F(" relay="));       out.print(pwr == PWR_ON || pwr == PWR_CLOSING ? 1 : 0);
    out.print(F(" pwr="));         out.print((int)pwr);
    out.print(F(" wantauto="));    out.print(serial_wants_auto ? 1 : 0);
    out.print(F(" link="));        out.print(serial_link_live() ? 1 : 0);
    out.print(F(" estoplatch="));  out.print(estop_latched ? 1 : 0);
    out.print(F(" esc="));         out.print(vbus > BUS_LOST_V ? 1 : 0);
    out.print(F(" vrail="));       out.print(vbus, 1);
    out.print(F(" v12="));         out.print(v12, 1);
    out.print(F(" sag="));         out.print(sag_active ? 1 : 0);
    out.print(F(" pwmL="));        out.print(last_pulse_l);
    out.print(F(" pwmR="));        out.print(last_pulse_r);
    out.print(F(" pwmlim="));      out.print(PWM_NEUTRAL_US + pwm_offset_limit());
    out.print(F(" thr="));         out.print(rc_ch[CH_THROTTLE]);
    out.print(F(" yaw="));         out.print(rc_ch[CH_YAW]);
    out.print(F(" ch7="));         out.print(CH_ARM >= 0 ? rc_ch[CH_ARM] : 0);
    out.print(F(" ch8="));         out.print(rc_ch[CH_MODE]);
    out.print(F(" rc="));          out.print(rc_link_ok() ? 1 : 0);
    out.print(F(" dtrc="));        out.print(now - last_rc_ms);
    out.print(F(" maxgap="));      out.print(rc_max_gap);
    out.print(F(" lq="));          out.print(rc_lq);
    out.print(F(" rssi="));        out.print(rc_rssi);
    out.print(F(" rcok="));        out.print(rc_frames);
    out.print(F(" rcbad="));       out.print(rc_bad);
    out.print(F(" jet="));         out.print(jetson_off_cmd ? 0 : 1);
    out.print(F(" pg="));          out.print(digitalRead(JETSON_PG_PIN));
    out.print(F(" leak="));        out.print(leak ? 1 : 0);
    out.print(F(" tenc="));        if (sht_ok) out.print(encl_t, 1); else out.print(F("nan"));
    out.print(F(" rh="));          if (sht_ok) out.print(encl_rh, 0); else out.print(F("nan"));
    out.print(F(" warn="));
    uint8_t w = 0;
    if (bus_stuck_warn) w |= 1;                                  // bus live long after opening
    if (v12 < V12_WARN_V) w |= 2;                                // 12 V rail low
    if (sht_ok && encl_t > ENCL_TEMP_WARN_C) w |= 4;             // enclosure hot
    if (sht_ok && encl_rh > ENCL_RH_WARN) w |= 8;                // enclosure humid
    if (!sht_ok) w |= 16;                                        // SHT4x not answering
    out.println(w);
  }
}
