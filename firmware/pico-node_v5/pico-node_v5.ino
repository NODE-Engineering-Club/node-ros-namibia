// ============================================================================
//  pico-node v5  -  Asket EC low-level controller  -  Pico 2 (RP2350)
//  Core: Earle Philhower arduino-pico  |  USB stack: Adafruit TinyUSB
//
//  v5 = v4 (firmware/pico-node_v4/, kept unchanged to roll back to) plus three
//  safety fixes (hw-alignment Phase B c). Everything else is v4, line for line.
//
//    1. RC LOST IN AUTONOMOUS. v4 only checked for SBUS silence outside
//       AUTONOMOUS, and kept the last decoded channels -- so a receiver that
//       stopped sending left the boat armed and autonomous with Ch8/Ch7 frozen
//       high. v5 treats "no valid frame for SBUS_FAILSAFE_TIMEOUT_MS" exactly
//       like the receiver's own failsafe bit, in every mode: ESTOP, relay open.
//    2. NO AUTOMATIC RE-ARM. v4's failsafe forced ESTOP, and ESTOP cleared the
//       latch -- so when the link came back with Ch7 still high the boat
//       re-armed by itself (and went straight back to AUTONOMOUS if the Jetson
//       still wanted it). v5 latches RC loss (rc_lost_latched) until the
//       operator puts Ch7 LOW on a live link, and drops the Jetson's AUTO
//       request: after re-arming, the boat is MANUAL until AUTO is asked again.
//    3. SERIAL OUTPUT NEVER BLOCKS. TinyUSB's CDC write() waits while the
//       host has the port open but is not reading. v5 queues every line in a
//       software ring and hands TinyUSB only what availableForWrite() says it
//       can take; a line that does not fit is dropped and counted (txdrop=).
//
//  GPIO20 E-stop feedback is NOT enabled (not wired). It stays behind
//  ESTOP_FEEDBACK_ENABLED, 0 by default.
//
//  Built from pico-node_v3 [REBUILT] and asket_ec_pico [REBUILT + FOXGLOVE].
//  See firmware/README.md for what came from where and what was dropped.
//
//  Modes (Ch8, 3-pos):  ESTOP / MANUAL / AUTONOMY-PERMITTED
//  Arm   (Ch7, 2-pos):  high = armed
//
//  MODE AUTHORITY -- the whole safety argument, in five lines:
//    Ch8 LOW    -> ESTOP            hardware priority, nothing overrides it
//    Ch8 MID    -> MANUAL forced    the Jetson cannot take autonomy here
//    Ch8 HIGH   -> autonomy PERMITTED, not granted:
//                    default MANUAL, until the Jetson sends "MODE AUTO",
//                    and only while its heartbeat is fresh.
//  Software can restrict what the transmitter allows. It can never extend it.
//
//  Manual: skid-steer mix of throttle (Ch3) + yaw (Ch4), pivot at hard-over.
//  Autonomous: "L R" / "L,R" motor pulses over USB serial.
//
//  Non-blocking throughout: no delay() anywhere, millis() only, the serial
//  reader is an accumulator rather than readStringUntil() -- see
//  handle_serial_input() -- and the serial writer is a ring -- see tx_pump().
//
//  TELLING VERSIONS APART is the one thing v3 and FOXGLOVE could not do, and
//  it cost months of a silently dead uplink. So this build announces itself:
//    * "[VER] pico-node 5" once at startup
//    * "ver=5" as the FIRST field of every STATE line
//  pico_bridge reads it, the GUI shows it, and pre-flight FAILS on a mismatch.
//  If you fork this file, bump FW_VERSION. If you change the wire format in a
//  way a parser would notice, you MUST bump it.
// ============================================================================

#include <Servo.h>
#include <Adafruit_TinyUSB.h>

// ---------------------------------------------------------------------------
//  PIN MAP  (unchanged from your board)
// ---------------------------------------------------------------------------
#define THRUSTER_LEFT_PIN   15
#define THRUSTER_RIGHT_PIN  16

#define RED_LIGHT_PIN       12
#define GREEN_LIGHT_PIN     10
#define YELLOW_LIGHT_PIN    11
#define BUZZER_PIN          13

#define ESTOP_RELAY_PIN     21   // OUT: HIGH = ESC power on, LOW = ESC power cut
#define ESTOP_READ_PIN      20   // IN : HIGH = rail present, LOW = no power (divider)

// ---------------------------------------------------------------------------
//  PWM (ESC) - bidirectional
// ---------------------------------------------------------------------------
const int PWM_MIN_US     = 1000;  // full reverse
const int PWM_NEUTRAL_US = 1500;  // stop
const int PWM_MAX_US     = 2000;  // full forward

// ---------------------------------------------------------------------------
//  SBUS CALIBRATION  (tune here)
//  Stick endpoints ~172 / ~991 (center) / ~1811
// ---------------------------------------------------------------------------
const uint16_t SBUS_MIN      = 172;
const uint16_t SBUS_MID      = 991;
const uint16_t SBUS_MAX      = 1811;
const uint16_t SBUS_DEADBAND = 30;   // counts around center treated as 0

// ---------------------------------------------------------------------------
//  MIXING TUNABLES
// ---------------------------------------------------------------------------
const float YAW_GAIN   = 0.7f;   // how aggressive turning is (0..1+)
const int   YAW_INVERT = +1;     // flip to -1 if it steers the wrong way
const int   THR_INVERT = +1;     // flip to -1 if forward/reverse are swapped

const float MANUAL_AUTHORITY = 1.0f;  // 1.0 = full 1000-2000; 0.8 = 1100-1900 (old code)
#define     PIVOT_AT_MAX_YAW   1       // 1 = spin in place when yaw is hard-over
const float PIVOT_THRESHOLD = 0.9f;    // fraction of yaw travel that triggers pivot

// ---------------------------------------------------------------------------
//  CHANNEL ASSIGNMENT  (0-indexed: Ch3 -> index 2, etc.)
// ---------------------------------------------------------------------------
const int CH_THROTTLE = 2;   // Ch3
const int CH_YAW      = 3;   // Ch4
const int CH_ARM      = 6;   // Ch7
const int CH_MODE     = 7;   // Ch8

const int ARM_THRESHOLD  = 1000;  // Ch7 above this = armed
const int MODE_LOW_MAX   = 700;   // Ch8 < this = ESTOP
const int MODE_MID_MAX   = 1400;  // Ch8 < this = MANUAL forced, else AUTONOMY PERMITTED

// ---------------------------------------------------------------------------
//  ESC ARMING / RELAY
// ---------------------------------------------------------------------------
const unsigned long ESC_ARM_DELAY_MS = 2000;  // hold neutral after relay closes

// ---------------------------------------------------------------------------
//  E-STOP FEEDBACK (GPIO20 voltage divider) - disabled for bench
// ---------------------------------------------------------------------------
// Not wired yet (the GPIO20 divider trace is cut). Prepared, not enabled: set
// to 1 only once the divider is fitted and checked on the bench, and set
// ESTOP_FEEDBACK_ENABLED in src/asket_common/asket_common/mode_arbitration.py
// to True in the same change (a test fails if the two disagree). The #ifndef
// lets the host tests build the enabled path without editing this line.
#ifndef ESTOP_FEEDBACK_ENABLED
#define ESTOP_FEEDBACK_ENABLED 0
#endif
const unsigned long ESTOP_GRACE_MS    = 500;
const unsigned long ESTOP_DEBOUNCE_MS = 100;

// NOTE: FOXGLOVE carried a BENCH_NO_RC_OVERRIDE compile flag here which, at 1,
// replaced the entire Ch8 zone logic with `serial_wants_auto ? AUTONOMOUS :
// MANUAL` and forced arm_high = true -- removing the hardware E-stop's
// authority outright. It is deliberately NOT carried into v4. A build flag
// that silently deletes the safety chain is one wrong upload away from a boat
// that cannot be stopped from the beach, and nothing distinguishes the two
// binaries once they are flashed. Bench-test with the receiver powered and the
// props out of the water, like everything else.

// ---------------------------------------------------------------------------
//  FIRMWARE IDENTITY
//  Bump FW_VERSION on any change a parser could notice. The Jetson refuses to
//  fly against a version it does not know how to read, which is the point.
// ---------------------------------------------------------------------------
#define FW_NAME    "pico-node"
#define FW_VERSION 5

// Longest line the Jetson ever sends is "MODE MANUAL" (11) or a motor command
// like "-0.123,-0.123" (13). 64 is generous and costs nothing.
#define RX_BUF_LEN 64

// ---------------------------------------------------------------------------
//  SBUS FRAME (manual parse, hardware inversion)
// ---------------------------------------------------------------------------
const int  NUM_CHANNELS    = 16;
const int  SBUS_FRAME_SIZE = 25;
const byte SBUS_SYNC_BYTE   = 0x0F;

byte     sbus_frame[SBUS_FRAME_SIZE] = {0};
uint16_t sbus_channels[NUM_CHANNELS] = {0};
bool     sbus_frame_ready = false;
int      sbus_frame_index = 0;
unsigned long last_sbus_read_ms   = 0;
// No valid frame for this long = the RC link is lost: ESTOP, in EVERY mode
// (v4 exempted AUTONOMOUS). 300 ms is 20+ frames at any SBUS rate and leaves
// room under the 500 ms budget for the loop to act on it.
const unsigned long SBUS_FAILSAFE_TIMEOUT_MS = 300;

// SBUS health counters, surfaced in the STATE line (see loop()).
unsigned long sbus_frames_ok  = 0;   // sync byte matched, channels decoded
unsigned long sbus_frames_bad = 0;   // sync byte mismatch (lost byte alignment)
bool          sbus_failsafe_flag = false;  // receiver-reported failsafe (footer bit 3)
bool          sbus_frame_lost_flag = false; // receiver-reported frame lost (footer bit 2)

// ---------------------------------------------------------------------------
//  AUTONOMOUS SERIAL FAILSAFE + HEARTBEAT
// ---------------------------------------------------------------------------
unsigned long last_serial_command_ms = 0;          // any motor command
const unsigned long SERIAL_FAILSAFE_TIMEOUT_MS = 500;

unsigned long last_heartbeat_ms = 0;               // PING or any valid serial line
const unsigned long HEARTBEAT_TIMEOUT_MS = 600;    // auto considered "live" within this

// ---------------------------------------------------------------------------
//  FOXGLOVE/Pi-REQUESTED SUB-MODE (only consulted when Ch8 HIGH)
//  Default MANUAL: Ch8 HIGH alone stays manual until the Pi asks for AUTO.
// ---------------------------------------------------------------------------
bool serial_wants_auto = false;     // set by "MODE AUTO" / cleared by "MODE MANUAL"

// ---------------------------------------------------------------------------
//  MODES + STATE
// ---------------------------------------------------------------------------
enum OperationMode { MODE_ESTOP = 1, MODE_MANUAL = 2, MODE_AUTONOMOUS = 3 };

OperationMode current_mode = MODE_ESTOP;
bool armed = false;

bool          relay_on      = false;
unsigned long relay_on_ms   = 0;

bool          estop_latched = false;   // power feedback (GPIO20) tripped

// RC link lost (SBUS silent, or the receiver's failsafe bit). Held until the
// operator puts Ch7 LOW while frames are arriving again: a link that comes
// back must not re-arm the boat on its own. See update_state().
//
// Starts SET: at power-up no link has been seen yet, so a boat switched on
// with Ch7 already high does not arm until the switch is cycled (v4 armed).
bool          rc_lost_latched = true;
bool          power_low_pending = false;
unsigned long power_low_since_ms = 0;

Servo thruster_left;
Servo thruster_right;

// ---------------------------------------------------------------------------
//  NON-BLOCKING BEEPER  (finite pulse train)
// ---------------------------------------------------------------------------
struct Beeper {
  bool          active   = false;
  int           remaining = 0;
  unsigned long onMs     = 0;
  unsigned long offMs    = 0;
  bool          isOn     = false;
  unsigned long phaseStart = 0;
} beeper;

void startBeeps(int count, unsigned long onMs, unsigned long offMs) {
  if (count <= 0) { beeper.active = false; digitalWrite(BUZZER_PIN, LOW); return; }
  beeper.active     = true;
  beeper.remaining  = count;
  beeper.onMs       = onMs;
  beeper.offMs      = offMs;
  beeper.isOn       = true;
  beeper.phaseStart = millis();
  digitalWrite(BUZZER_PIN, HIGH);
}

void updateBeeper() {
  if (!beeper.active) return;
  unsigned long now = millis();
  if (beeper.isOn) {
    if (now - beeper.phaseStart >= beeper.onMs) {
      digitalWrite(BUZZER_PIN, LOW);
      beeper.isOn = false;
      beeper.phaseStart = now;
      beeper.remaining--;
      if (beeper.remaining <= 0) beeper.active = false;  // done after last ON
    }
  } else {
    if (now - beeper.phaseStart >= beeper.offMs) {
      digitalWrite(BUZZER_PIN, HIGH);
      beeper.isOn = true;
      beeper.phaseStart = now;
    }
  }
}

// ---------------------------------------------------------------------------
//  NON-BLOCKING SERIAL OUTPUT  (Pico -> Jetson)
//
//  Every line goes through tx_begin()/tx_add()/tx_end() into a software ring;
//  tx_pump() (every loop) hands TinyUSB only as many bytes as
//  availableForWrite() reports free. TinyUSB's own write() spins while the
//  host has the port open and is not reading -- with v4's direct
//  Serial.print() that froze loop(), and with it SBUS, the motors and every
//  failsafe. Nothing in this file calls Serial.print/println, and tx_pump() is
//  the only caller of Serial.write().
//
//  A line that does not fit in the ring is dropped whole (never cut in half)
//  and counted in tx_dropped, reported as txdrop= in the STATE line.
// ---------------------------------------------------------------------------
#define TX_LINE_LEN 200    // longest line: STATE is ~200 characters
#define TX_RING_LEN 1024   // ~5 STATE lines of slack while the host catches up

char          tx_line[TX_LINE_LEN];
size_t        tx_line_len  = 0;
bool          tx_line_cut  = false;
uint8_t       tx_ring[TX_RING_LEN];
size_t        tx_ring_head = 0;     // next byte written
size_t        tx_ring_tail = 0;     // next byte sent
size_t        tx_ring_used = 0;
unsigned long tx_dropped   = 0;

void tx_begin() { tx_line_len = 0; tx_line_cut = false; }

void tx_add(const char *s) {
  while (*s) {
    if (tx_line_len < TX_LINE_LEN - 1) tx_line[tx_line_len++] = *s;
    else                               tx_line_cut = true;
    s++;
  }
}
void tx_add(char c)            { char b[2] = { c, 0 }; tx_add(b); }
void tx_add(int v)             { char b[16]; snprintf(b, sizeof b, "%d", v);  tx_add(b); }
void tx_add(unsigned int v)    { char b[16]; snprintf(b, sizeof b, "%u", v);  tx_add(b); }
void tx_add(unsigned long v)   { char b[24]; snprintf(b, sizeof b, "%lu", v); tx_add(b); }

// Queue the line (plus '\n') or drop it whole.
void tx_end() {
  if (tx_line_cut || tx_line_len + 1 > TX_RING_LEN - tx_ring_used) {
    tx_dropped++;
    return;
  }
  tx_line[tx_line_len++] = '\n';
  for (size_t i = 0; i < tx_line_len; i++) {
    tx_ring[tx_ring_head] = (uint8_t)tx_line[i];
    tx_ring_head = (tx_ring_head + 1) % TX_RING_LEN;
  }
  tx_ring_used += tx_line_len;
}

void say(const char *s) { tx_begin(); tx_add(s); tx_end(); }

// Send what TinyUSB can take right now, and not a byte more.
void tx_pump() {
  while (tx_ring_used > 0) {
    int room = Serial.availableForWrite();
    if (room <= 0) return;
    size_t chunk = tx_ring_used;
    if (chunk > (size_t)room) chunk = (size_t)room;
    if (chunk > TX_RING_LEN - tx_ring_tail) chunk = TX_RING_LEN - tx_ring_tail;  // up to the wrap
    size_t sent = Serial.write(&tx_ring[tx_ring_tail], chunk);
    if (sent == 0) return;
    tx_ring_tail = (tx_ring_tail + sent) % TX_RING_LEN;
    tx_ring_used -= sent;
  }
}

// ---------------------------------------------------------------------------
//  PROTOTYPES
// ---------------------------------------------------------------------------
void  init_gpio();
void  startup_light_sequence();
void  set_light(OperationMode mode, bool live);
void  handle_sbus();
void  parse_sbus_frame();
void  update_state();
void  apply_transition(OperationMode new_mode, bool want_armed);
void  set_relay(bool on);
void  update_motors();
void  handle_serial_input();
void  handle_serial_line(const String &line);
bool  process_motor_command(const String &cmd, int &left, int &right);
void  check_power_feedback();
void  trigger_estop(const char *reason);
float sbus_to_norm(uint16_t v);
void  write_thrusters(int left, int right);
void  set_both(int pulse);
bool  serial_link_live();

// ============================================================================
//  SETUP
// ============================================================================
void setup() {
  Serial.begin(115200);

  // Wait (non-blocking) up to 3 s for USB CDC to mount
  unsigned long t0 = millis();
  while (!TinyUSBDevice.mounted() && (millis() - t0 < 3000)) { /* spin */ }

  init_gpio();

  // SBUS receiver on Serial2 (UART1) with hardware inversion
  Serial2.setRX(5);
  Serial2.setTX(4);
  Serial2.setInvertRX(true);
  Serial2.begin(100000, SERIAL_8E2);

  thruster_left.attach(THRUSTER_LEFT_PIN,  PWM_MIN_US, PWM_MAX_US);
  thruster_right.attach(THRUSTER_RIGHT_PIN, PWM_MIN_US, PWM_MAX_US);
  set_both(PWM_NEUTRAL_US);

  last_serial_command_ms = millis();
  last_sbus_read_ms      = millis();
  last_heartbeat_ms      = millis();

  startup_light_sequence();      // millis-based, non-blocking
  set_light(MODE_ESTOP, false);  // start safe: red

  // Machine-readable identity, before any human-readable banner, so a host
  // that opens the port at boot learns what it is talking to immediately
  // rather than waiting up to 250 ms for the first STATE line.
  tx_begin(); tx_add("[VER] "); tx_add(FW_NAME); tx_add(' '); tx_add(FW_VERSION); tx_end();

  say("=== Asket EC Pico Controller (pico-node v5) ===");
  say("Ch8 LOW=ESTOP  MID=MANUAL  HIGH=autonomy-permitted");
  say("Serial: 'L R' or 'L,R' motors | 'MODE AUTO' | 'MODE MANUAL' | 'PING'");
  say(ESTOP_FEEDBACK_ENABLED ? "E-STOP feedback: ENABLED" : "E-STOP feedback: DISABLED (not wired)");
  say("RC lost (SBUS silent or failsafe) = E-STOP in every mode; re-arm needs Ch7 LOW then HIGH");
  say("====================================================");
  tx_pump();
}

// ============================================================================
//  MAIN LOOP
// ============================================================================
void loop() {
  unsigned long now = millis();

  handle_sbus();              // read + parse SBUS
  // Always drained, in every mode. v3 gated this on (AUTONOMOUS && armed),
  // which meant nothing the Jetson sent was ever consumed the rest of the
  // time -- so MODE/PING could not work, and whatever the host wrote piled up
  // in the USB buffer until it blocked, was dropped, or was replayed stale on
  // the next mode change. Draining costs microseconds; the verbs are gated by
  // update_state(), not by whether we bothered to read them.
  handle_serial_input();
  update_state();             // arm / mode transitions
  check_power_feedback();     // e-stop divider (disabled until hardware ready)

  updateBeeper();
  update_motors();

  // --- Failsafes ---
  // RC loss (SBUS silent or receiver failsafe) is handled in update_state(),
  // in every mode: v4 checked it here and only outside AUTONOMOUS.
  if (armed) {
    // Serial/motor lost in autonomous: hold neutral, do NOT kill power. The
    // transmitter is still there and the operator can take manual; cutting the
    // relay would cost another 2 s arm window before they could manoeuvre.
    if (current_mode == MODE_AUTONOMOUS &&
        (now - last_serial_command_ms > SERIAL_FAILSAFE_TIMEOUT_MS)) {
      set_both(PWM_NEUTRAL_US);
    }
  }

  // --- Periodic status (parsable for Foxglove) ---
  static unsigned long last_status = 0;
  if (now - last_status > 250) {
    // Machine-readable line. Fields are key=value, space separated.
    //
    // ver= is FIRST and deliberately so: a parser that does not recognise the
    // version can reject the line before it misreads a single field. Bump
    // FW_VERSION whenever anything below this comment changes shape.
    //
    // v5 adds two fields at the end: rclatch (RC loss latched, waiting for
    // Ch7 LOW) and txdrop (lines this build had to drop because the host was
    // not reading).
    tx_begin();
    tx_add("STATE ver=");    tx_add(FW_VERSION);
    tx_add(" mode=");        tx_add((int)current_mode);
    tx_add(" armed=");       tx_add(armed ? 1 : 0);
    tx_add(" relay=");       tx_add(relay_on ? 1 : 0);
    tx_add(" wantauto=");    tx_add(serial_wants_auto ? 1 : 0);
    tx_add(" link=");        tx_add(serial_link_live() ? 1 : 0);
    tx_add(" estoplatch=");  tx_add(estop_latched ? 1 : 0);
    tx_add(" thr=");         tx_add((unsigned int)sbus_channels[CH_THROTTLE]);
    tx_add(" yaw=");         tx_add((unsigned int)sbus_channels[CH_YAW]);
    tx_add(" ch7=");         tx_add((unsigned int)sbus_channels[CH_ARM]);
    tx_add(" ch8=");         tx_add((unsigned int)sbus_channels[CH_MODE]);
    tx_add(" sbusok=");      tx_add(sbus_frames_ok);
    tx_add(" sbusbad=");     tx_add(sbus_frames_bad);
    tx_add(" sbusfs=");      tx_add(sbus_failsafe_flag ? 1 : 0);
    tx_add(" sbuslost=");    tx_add(sbus_frame_lost_flag ? 1 : 0);
    tx_add(" rclatch=");     tx_add(rc_lost_latched ? 1 : 0);
    tx_add(" txdrop=");      tx_add(tx_dropped);
    tx_end();
    last_status = now;
  }

  tx_pump();   // last, so this loop's lines leave as soon as TinyUSB has room
}

// ============================================================================
//  GPIO
// ============================================================================
void init_gpio() {
  pinMode(RED_LIGHT_PIN,    OUTPUT);
  pinMode(GREEN_LIGHT_PIN,  OUTPUT);
  pinMode(YELLOW_LIGHT_PIN, OUTPUT);
  pinMode(BUZZER_PIN,       OUTPUT);
  pinMode(ESTOP_RELAY_PIN,  OUTPUT);
  pinMode(ESTOP_READ_PIN,   INPUT);   // external divider sets the level; break = LOW = safe

  digitalWrite(ESTOP_RELAY_PIN, LOW); // relay off = ESC power cut at boot
  relay_on = false;
  digitalWrite(RED_LIGHT_PIN,    LOW);
  digitalWrite(GREEN_LIGHT_PIN,  LOW);
  digitalWrite(YELLOW_LIGHT_PIN, LOW);
  digitalWrite(BUZZER_PIN,       LOW);
}

void startup_light_sequence() {
  const int   pins[3] = { RED_LIGHT_PIN, YELLOW_LIGHT_PIN, GREEN_LIGHT_PIN };
  const int   steps   = 6;     // 2 cycles x 3 colors
  const unsigned long step_ms = 150;
  unsigned long start = millis();
  int last_step = -1;
  while (true) {
    unsigned long e = millis() - start;
    int step = e / step_ms;
    if (step >= steps) break;
    if (step != last_step) {
      digitalWrite(RED_LIGHT_PIN,    LOW);
      digitalWrite(YELLOW_LIGHT_PIN, LOW);
      digitalWrite(GREEN_LIGHT_PIN,  LOW);
      digitalWrite(pins[step % 3], HIGH);
      last_step = step;
    }
  }
  digitalWrite(RED_LIGHT_PIN,    LOW);
  digitalWrite(YELLOW_LIGHT_PIN, LOW);
  digitalWrite(GREEN_LIGHT_PIN,  LOW);
}

void set_light(OperationMode mode, bool live) {
  digitalWrite(RED_LIGHT_PIN,    LOW);
  digitalWrite(YELLOW_LIGHT_PIN, LOW);
  digitalWrite(GREEN_LIGHT_PIN,  LOW);
  if (!live || mode == MODE_ESTOP) { digitalWrite(RED_LIGHT_PIN, HIGH); return; }
  if (mode == MODE_MANUAL)         { digitalWrite(YELLOW_LIGHT_PIN, HIGH); return; }
  if (mode == MODE_AUTONOMOUS)     { digitalWrite(GREEN_LIGHT_PIN,  HIGH); return; }
}

// ============================================================================
//  SBUS
// ============================================================================
void handle_sbus() {
  unsigned long now = millis();
  while (Serial2.available()) {
    byte b = Serial2.read();
    if (sbus_frame_index == 0 && b == SBUS_SYNC_BYTE) {
      sbus_frame[0] = b;
      sbus_frame_index = 1;
    } else if (sbus_frame_index > 0) {
      sbus_frame[sbus_frame_index++] = b;
      if (sbus_frame_index >= SBUS_FRAME_SIZE) {
        parse_sbus_frame();
        if (sbus_frame_ready) { last_sbus_read_ms = now; sbus_frame_ready = false; }
        sbus_frame_index = 0;
      }
    }
  }
}

void parse_sbus_frame() {
  // FIX: only the sync byte identifies a valid frame. The old footer check
  // (`sbus_frame[24] != 0x00`) rejected every frame whose flags byte had any
  // bit set -- but that byte legitimately carries CH17/CH18 digital-channel
  // states plus frame-lost/failsafe flags, so on most receivers it is *never*
  // reliably 0x00. That silently dropped 100% of frames on a perfectly
  // healthy SBUS link.
  if (sbus_frame[0] != SBUS_SYNC_BYTE) { sbus_frames_bad++; return; }

  sbus_channels[0]  = ((sbus_frame[1]      | sbus_frame[2]  << 8) & 0x7FF);
  sbus_channels[1]  = ((sbus_frame[2]  >> 3| sbus_frame[3]  << 5) & 0x7FF);
  sbus_channels[2]  = ((sbus_frame[3]  >> 6| sbus_frame[4]  << 2 | sbus_frame[5] << 10) & 0x7FF);
  sbus_channels[3]  = ((sbus_frame[5]  >> 1| sbus_frame[6]  << 7) & 0x7FF);
  sbus_channels[4]  = ((sbus_frame[6]  >> 4| sbus_frame[7]  << 4) & 0x7FF);
  sbus_channels[5]  = ((sbus_frame[7]  >> 7| sbus_frame[8]  << 1 | sbus_frame[9] << 9) & 0x7FF);
  sbus_channels[6]  = ((sbus_frame[9]  >> 2| sbus_frame[10] << 6) & 0x7FF);
  sbus_channels[7]  = ((sbus_frame[10] >> 5| sbus_frame[11] << 3) & 0x7FF);
  sbus_channels[8]  = ((sbus_frame[12]     | sbus_frame[13] << 8) & 0x7FF);
  sbus_channels[9]  = ((sbus_frame[13] >> 3| sbus_frame[14] << 5) & 0x7FF);
  sbus_channels[10] = ((sbus_frame[14] >> 6| sbus_frame[15] << 2 | sbus_frame[16] << 10) & 0x7FF);
  sbus_channels[11] = ((sbus_frame[16] >> 1| sbus_frame[17] << 7) & 0x7FF);
  sbus_channels[12] = ((sbus_frame[17] >> 4| sbus_frame[18] << 4) & 0x7FF);
  sbus_channels[13] = ((sbus_frame[18] >> 7| sbus_frame[19] << 1 | sbus_frame[20] << 9) & 0x7FF);
  sbus_channels[14] = ((sbus_frame[20] >> 2| sbus_frame[21] << 6) & 0x7FF);
  sbus_channels[15] = ((sbus_frame[21] >> 5| sbus_frame[22] << 3) & 0x7FF);

  // Flags byte (index 23): bit2 = frame lost, bit3 = failsafe. Surfaced for
  // diagnostics; a transient frame-lost bit doesn't invalidate the frame.
  byte flags = sbus_frame[23];
  sbus_frame_lost_flag = flags & 0x04;
  sbus_failsafe_flag   = flags & 0x08;

  sbus_frames_ok++;
  sbus_frame_ready = true;
}

// ============================================================================
//  STATE MACHINE  (final authority spec)
// ============================================================================
bool serial_link_live() {
  return (millis() - last_heartbeat_ms) <= HEARTBEAT_TIMEOUT_MS;
}

void update_state() {
  uint16_t arm_ch  = sbus_channels[CH_ARM];
  uint16_t mode_ch = sbus_channels[CH_MODE];

  // RC link lost: no valid frame recently (the channels below are then stale
  // copies of the last frame and mean nothing), or the receiver says so.
  bool sbus_silent = (millis() - last_sbus_read_ms) > SBUS_FAILSAFE_TIMEOUT_MS;
  bool rc_lost     = sbus_silent || sbus_failsafe_flag;

  // --- Resolve Ch8 zone ---
  OperationMode new_mode;
  if (mode_ch < MODE_LOW_MAX) {
    // ZONE 1: hardware ESTOP, absolute priority. Nothing below can override.
    new_mode = MODE_ESTOP;
  } else if (mode_ch < MODE_MID_MAX) {
    // ZONE 2: MANUAL forced. The Pi cannot take autonomy here.
    new_mode = MODE_MANUAL;
  } else {
    // ZONE 3: autonomy PERMITTED. Default MANUAL until the Pi asks for AUTO,
    // and only while the serial link is alive (heartbeat fresh).
    if (serial_wants_auto && serial_link_live()) new_mode = MODE_AUTONOMOUS;
    else                                         new_mode = MODE_MANUAL;
  }
  // A lost RC link (silent or receiver-declared failsafe) overrides
  // everything, in every mode. Written after the zones so no branch above can
  // bypass it.
  if (rc_lost) new_mode = MODE_ESTOP;

  bool arm_high = (arm_ch > ARM_THRESHOLD);

  // Latch the loss. Entering it also withdraws the Jetson's AUTO request, so
  // a re-armed boat comes back MANUAL and autonomy has to be asked for again.
  // The latch clears only on Ch7 LOW seen in a live frame -- the operator's
  // own act, on a link that is back -- never on its own.
  if (rc_lost) {
    if (!rc_lost_latched) {
      rc_lost_latched   = true;
      serial_wants_auto = false;
      say(sbus_silent ? "[E-STOP] RC link lost: no SBUS frame"
                      : "[E-STOP] RC link lost: receiver failsafe");
    }
  } else if (!arm_high && rc_lost_latched) {
    rc_lost_latched = false;
    say("[RC] link back and Ch7 low: may arm again");
  }

  bool want_armed = arm_high && (new_mode != MODE_ESTOP);

  // Clear the power-loss latch when operator disarms or selects ESTOP
  if (!arm_high || new_mode == MODE_ESTOP) estop_latched = false;
  if (estop_latched || rc_lost_latched) want_armed = false;

  if (new_mode != current_mode || want_armed != armed) {
    apply_transition(new_mode, want_armed);
  }
}

void apply_transition(OperationMode new_mode, bool want_armed) {
  set_both(PWM_NEUTRAL_US);     // always neutral on any change first

  current_mode = new_mode;
  armed        = want_armed;

  if (current_mode == MODE_ESTOP) {
    set_relay(false);
    set_light(MODE_ESTOP, false);
    startBeeps(1, 1000, 0);
    say(">>> MODE: E-STOP");
    return;
  }

  if (armed) {
    set_relay(true);                            // energize (resets arm window if was off)
    set_light(current_mode, true);
    if (current_mode == MODE_MANUAL)      startBeeps(2, 200, 200);   // 2 short
    else                                  startBeeps(3, 150, 150);   // 3 short
    say(current_mode == MODE_MANUAL ? ">>> ARMED: MANUAL" : ">>> ARMED: AUTONOMOUS");
  } else {
    set_relay(false);
    set_light(current_mode, false);             // red while disarmed
    say(current_mode == MODE_MANUAL ? ">>> DISARMED (manual)" : ">>> DISARMED (auto)");
  }
}

void set_relay(bool on) {
  if (on) {
    if (!relay_on) {                 // only stamp the timer on a real off->on edge
      digitalWrite(ESTOP_RELAY_PIN, HIGH);
      relay_on    = true;
      relay_on_ms = millis();
      power_low_pending = false;
    }
  } else {
    digitalWrite(ESTOP_RELAY_PIN, LOW);
    relay_on = false;
    power_low_pending = false;
  }
}

// ============================================================================
//  MOTOR OUTPUT
// ============================================================================
void update_motors() {
  if (!armed) { set_both(PWM_NEUTRAL_US); return; }

  // ESC cold-boot: hold neutral until the arming window elapses
  if (millis() - relay_on_ms < ESC_ARM_DELAY_MS) { set_both(PWM_NEUTRAL_US); return; }

  if (current_mode == MODE_MANUAL) {
    float thr     = sbus_to_norm(sbus_channels[CH_THROTTLE]) * THR_INVERT;
    float yaw_raw = sbus_to_norm(sbus_channels[CH_YAW]) * YAW_INVERT;  // pre-gain, for pivot test
    float yaw     = yaw_raw * YAW_GAIN;

    float left, right;

#if PIVOT_AT_MAX_YAW
    if (fabs(yaw_raw) >= PIVOT_THRESHOLD) {
      // Hard-over yaw: pivot in place with full opposite thrust
      left  = (yaw_raw > 0) ? +1.0f : -1.0f;
      right = -left;
    } else
#endif
    {
      left  = thr + yaw;                       // skid-steer mix
      right = thr - yaw;
      // Proportional normalize: keep the turn ratio instead of clipping one side
      float m = max(fabs(left), fabs(right));
      if (m > 1.0f) { left /= m; right /= m; }
    }

    int span = (int)(500.0f * MANUAL_AUTHORITY);
    write_thrusters(PWM_NEUTRAL_US + (int)(left  * span),
                    PWM_NEUTRAL_US + (int)(right * span));
  }
  // AUTONOMOUS thrust is written directly in handle_serial_input()
}

float sbus_to_norm(uint16_t v) {
  int c = (int)v - (int)SBUS_MID;
  if (abs(c) < (int)SBUS_DEADBAND) return 0.0f;
  float n;
  if (c > 0) n = (float)(c - SBUS_DEADBAND) / (float)((int)SBUS_MAX - (int)SBUS_MID - (int)SBUS_DEADBAND);
  else       n = (float)(c + SBUS_DEADBAND) / (float)((int)SBUS_MID - (int)SBUS_MIN - (int)SBUS_DEADBAND);
  return constrain(n, -1.0f, 1.0f);
}

void set_both(int pulse) {
  thruster_left.writeMicroseconds(pulse);
  thruster_right.writeMicroseconds(pulse);
}

void write_thrusters(int left, int right) {
  left  = constrain(left,  PWM_MIN_US, PWM_MAX_US);
  right = constrain(right, PWM_MIN_US, PWM_MAX_US);
  thruster_left.writeMicroseconds(left);
  thruster_right.writeMicroseconds(right);
}

// ============================================================================
//  SERIAL INPUT  (Pi -> Pico): motor values, MODE verbs, PING heartbeat
// ============================================================================
// Accumulate bytes until a newline, then act on one complete line.
//
// v3 and FOXGLOVE both used Serial.readStringUntil('\n'), which blocks until
// the terminator arrives or the 1000 ms stream timeout expires. A line that
// straddles two USB packets costs a millisecond; a Jetson that dies mid-line
// costs a full second. During that second loop() does not run, so SBUS is not
// read, the motors are not updated, and NEITHER FAILSAFE IS EVALUATED -- while
// the thrusters hold their last commanded pulse. A one-second stall is exactly
// what a 500 ms failsafe exists to prevent, so the reader must never block.
//
// This drains whatever is available and returns immediately. A line longer
// than the buffer is truncated and dropped rather than silently split into two
// half-commands, because half of "0.8,-0.8" is a valid command for one motor.
void handle_serial_input() {
  static char    rx[RX_BUF_LEN];
  static uint8_t rx_len      = 0;
  static bool    rx_overflow = false;

  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c != '\n' && c != '\r') {
      if (rx_len < RX_BUF_LEN - 1) rx[rx_len++] = c;
      else                         rx_overflow = true;   // poisoned; drop at EOL
      continue;
    }
    if (rx_len == 0 && !rx_overflow) continue;           // bare newline

    rx[rx_len] = '\0';
    String line(rx);
    rx_len = 0;
    bool overflowed = rx_overflow;
    rx_overflow = false;

    if (overflowed) {
      say("Invalid cmd: line too long");
      continue;
    }
    line.trim();
    if (line.length() == 0) continue;
    handle_serial_line(line);
  }
}

// One complete line from the Jetson.
void handle_serial_line(const String &line) {
  // Any valid line from the Pi counts as a heartbeat.
  last_heartbeat_ms = millis();

  // --- Mode verbs (effective only where update_state() allows) ---
  String up = line;
  up.toUpperCase();

  if (up == "PING") {
    return;  // heartbeat only -- last_heartbeat_ms is already stamped above
  }
  if (up == "MODE AUTO") {
    // Refused while the RC-lost latch is held: otherwise a request sent
    // during the outage would put the boat straight back in AUTONOMOUS the
    // moment the operator re-arms. Autonomy is asked for again afterwards.
    if (rc_lost_latched) {
      serial_wants_auto = false;
      say("[NAK] MODE AUTO refused: RC link lost, cycle Ch7 first");
      return;
    }
    serial_wants_auto = true;
    say("[ACK] MODE AUTO requested");
    return;
  }
  if (up == "MODE MANUAL") {
    serial_wants_auto = false;
    say("[ACK] MODE MANUAL requested");
    return;
  }

  // --- Otherwise: motor command (bare values, unchanged behavior) ---
  // Only act on motors when actually armed & autonomous & past arm window.
  int l, r;
  if (process_motor_command(line, l, r)) {
    last_serial_command_ms = millis();
    if (armed && current_mode == MODE_AUTONOMOUS &&
        (millis() - relay_on_ms >= ESC_ARM_DELAY_MS)) {
      write_thrusters(l, r);
    }
    // If not in autonomous, command is accepted as heartbeat but NOT applied.
  } else {
    tx_begin(); tx_add("Invalid cmd: "); tx_add(line.c_str()); tx_end();
  }
}

bool process_motor_command(const String &command, int &left, int &right) {
  String cmd = command;
  cmd.replace('[', ' '); cmd.replace(']', ' ');
  cmd.replace(',', ' '); cmd.replace(';', ' ');
  cmd.trim();

  float v1 = 0, v2 = 0;
  int n = sscanf(cmd.c_str(), "%f %f", &v1, &v2);
  if (n >= 1) {
    // If values look normalized (-1..1) treat as norm, else as raw microseconds
    bool norm = (fabs(v1) <= 1.5f) && (n == 1 || fabs(v2) <= 1.5f);
    if (norm) {
      left  = PWM_NEUTRAL_US + (int)(constrain(v1, -1.0f, 1.0f) * 500.0f);
      right = (n == 2) ? PWM_NEUTRAL_US + (int)(constrain(v2, -1.0f, 1.0f) * 500.0f) : left;
    } else {
      left  = constrain((int)v1, PWM_MIN_US, PWM_MAX_US);
      right = (n == 2) ? constrain((int)v2, PWM_MIN_US, PWM_MAX_US) : left;
    }
    return true;
  }
  return false;
}

// ============================================================================
//  E-STOP POWER FEEDBACK  (disabled until hardware ready)
// ============================================================================
void check_power_feedback() {
#if ESTOP_FEEDBACK_ENABLED
  if (!armed || !relay_on) { power_low_pending = false; return; }
  // Let the relay close + divider settle before trusting the read
  if (millis() - relay_on_ms < ESTOP_GRACE_MS) { power_low_pending = false; return; }

  bool powered = (digitalRead(ESTOP_READ_PIN) == HIGH);
  if (powered) {
    power_low_pending = false;
  } else {
    unsigned long now = millis();
    if (!power_low_pending) { power_low_pending = true; power_low_since_ms = now; }
    else if (now - power_low_since_ms >= ESTOP_DEBOUNCE_MS) {
      trigger_estop("power feedback LOW");
    }
  }
#else
  (void)power_low_pending;  // feature off: never disarms on the bench
#endif
}

void trigger_estop(const char *reason) {
  armed = false;
  estop_latched = true;          // block auto re-arm until operator cycles arm/mode
  set_relay(false);
  set_both(PWM_NEUTRAL_US);
  set_light(MODE_ESTOP, false);
  startBeeps(1, 1000, 0);
  tx_begin(); tx_add("[E-STOP] "); tx_add(reason); tx_end();
}
