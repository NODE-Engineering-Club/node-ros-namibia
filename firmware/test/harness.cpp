// Host-side tests for pico-node_v5. See Makefile for what this can and
// cannot tell you. Built twice: as shipped (GPIO20 feedback off), and with
// -DESTOP_FEEDBACK_ENABLED=1 so the prepared-but-unwired feedback path is
// exercised too.
#include "ArduinoStub.h"
unsigned long g_millis = 0;
bool g_auto_ms = true;
std::vector<int> g_pin_state(32, 0);
SerialStub Serial;
SerialStub Serial2;
TinyUSBDeviceStub TinyUSBDevice;
#define Servo_h
#include "sketch.inc"

// ---- test helpers -------------------------------------------------------
static void tick(unsigned long ms) { for (unsigned long i = 0; i < ms; i++) { g_millis++; loop(); } }
// Clear first, then settle: a phase shorter than the 250 ms status throttle
// would otherwise assert against the PREVIOUS phase's line.
static std::string lastStateLine() {
  std::string best;
  size_t p = 0;
  while ((p = Serial.out.find("STATE ", p)) != std::string::npos) {
    size_t e = Serial.out.find('\n', p);
    if (e == std::string::npos) break;          // a line still being sent
    best = Serial.out.substr(p, e - p);
    p = e;
  }
  return best;
}
static void feedSbus(uint16_t ch7, uint16_t ch8, uint16_t thr = 991, uint16_t yaw = 991,
                     uint8_t flags = 0x00) {
  // Build a 25-byte SBUS frame with all 16 channels, then push it at Serial2.
  uint16_t ch[16]; for (int i = 0; i < 16; i++) ch[i] = 991;
  ch[CH_THROTTLE] = thr; ch[CH_YAW] = yaw; ch[CH_ARM] = ch7; ch[CH_MODE] = ch8;
  uint8_t f[25] = {0}; f[0] = 0x0F;
  int bit = 0;
  for (int c = 0; c < 16; c++)
    for (int b = 0; b < 11; b++, bit++)
      if (ch[c] & (1 << b)) f[1 + bit / 8] |= (1 << (bit % 8));
  f[23] = flags; f[24] = 0x04;   // FrSky-style end byte: v3 would reject this
  for (int i = 0; i < 25; i++) Serial2.in.push_back((char)f[i]);
}
// `ms` of a healthy link: a frame every 10 ms, a heartbeat every 50 ms.
static void run(unsigned long ms, uint16_t ch7, uint16_t ch8, const char* jetson = "PING",
                uint8_t flags = 0x00) {
  for (unsigned long t = 0; t < ms; t += 10) {
    feedSbus(ch7, ch8, 991, 991, flags);
    if (jetson && (t % 50) == 0) { Serial.feed(jetson); Serial.feed("\n"); }
    tick(10);
  }
}
static bool relayOpen() { return g_pin_state[ESTOP_RELAY_PIN] == LOW; }
static bool thrustersNeutral() {
  return thruster_left.last == PWM_NEUTRAL_US && thruster_right.last == PWM_NEUTRAL_US;
}
// Arm in AUTONOMOUS, past the 2 s ESC window, with the Jetson driving.
static void getAutonomous() {
  run(300, 200, 1811);                        // Ch7 low first: clears any boot latch
  Serial.feed("MODE AUTO\n");
  run(2600, 1811, 1811, "0.5,0.5");          // armed, AUTONOMOUS, past arm window
}

static int failures = 0;
static void check(bool ok, const char* what) {
  printf("%s  %s\n", ok ? "  PASS" : "  FAIL", what);
  if (!ok) failures++;
}
static bool has(const std::string& hay, const char* needle) {
  return hay.find(needle) != std::string::npos;
}

int main() {
#if ESTOP_FEEDBACK_ENABLED
  printf("== build: ESTOP_FEEDBACK_ENABLED=1 (GPIO20 feedback path) ==\n");
  g_pin_state[ESTOP_READ_PIN] = HIGH;          // rail present unless a test drops it
#else
  printf("== build: as shipped (GPIO20 feedback disabled) ==\n");
#endif
  setup();
  g_auto_ms = false;
  printf("[boot banner]\n%s\n", Serial.out.c_str());
  check(has(Serial.out, "[VER] pico-node 5"), "startup announces [VER] pico-node 5");
  check(has(Serial.out, "(pico-node v5)"), "banner says v5");
  check(!has(Serial.out, "BENCH"), "no bench-override banner");
  check(has(Serial.out, ESTOP_FEEDBACK_ENABLED ? "E-STOP feedback: ENABLED"
                                               : "E-STOP feedback: DISABLED"),
        "banner states the GPIO20 feedback setting");

  // =====================================================================
  //  v4 behaviour, unchanged
  // =====================================================================

  // --- boot: the receiver was not there yet, so the RC-lost latch is set and
  //     Ch7 high alone must not arm. Ch7 low, then high, does.
  Serial.out.clear();
  run(400, 1811, 1811);
  std::string st = lastStateLine();
  check(has(st, " armed=0") && has(st, " rclatch=1"),
        "power-up with Ch7 already high does not arm (latched until Ch7 low)");
  run(100, 200, 1811);
  run(400, 1811, 1811);
  st = lastStateLine();
  printf("[state] %s\n", st.c_str());
  check(st.rfind("STATE ver=5 ", 0) == 0, "ver=5 is the first STATE field");
  check(has(st, " mode=2 "), "Ch8 high alone stays MANUAL (autonomy permitted, not granted)");
  check(has(st, " armed=1"), "Ch7 low then high arms");
  check(has(st, " rclatch=0"), "latch reported cleared");
  check(has(st, " txdrop=0"), "txdrop reported, nothing dropped");
  check(has(st, "sbusok="), "SBUS counters present");
  check(!has(st, "sbusok=0 "), "frames with a non-zero end byte are accepted (v3 rejected these)");

  // --- MODE AUTO is honoured in the high zone
  Serial.feed("MODE AUTO\n");
  run(600, 1811, 1811);
  st = lastStateLine();
  check(has(st, " mode=3 "), "MODE AUTO grants autonomy in the high zone");
  check(has(st, " wantauto=1"), "wantauto reported");
  check(has(st, " link=1"), "heartbeat fresh -> link=1");

  // --- Ch8 mid forces MANUAL even though the Jetson still wants auto
  run(600, 1811, 1000);
  st = lastStateLine();
  check(has(st, " mode=2 "), "Ch8 mid forces MANUAL over a standing MODE AUTO");
  check(has(st, " wantauto=1"), "...and the request is still visible, not silently dropped");

  // --- Ch8 low is ESTOP and disarms
  run(600, 1811, 200);
  st = lastStateLine();
  check(has(st, " mode=1 "), "Ch8 low forces ESTOP");
  check(has(st, " armed=0"), "ESTOP disarms");
  check(relayOpen(), "ESTOP opens the relay");
  check(g_pin_state[RED_LIGHT_PIN] == HIGH && g_pin_state[GREEN_LIGHT_PIN] == LOW, "ESTOP shows red only");

  // --- the reader never blocks on a partial line
  Serial.out.clear();
  Serial.feed("0.5,-0.5");          // no newline: half a command
  unsigned long t0 = g_millis;
  for (int i = 0; i < 5; i++) { feedSbus(1811, 1811); tick(10); }
  check(g_millis - t0 == 50, "a partial line does not stall the loop");
  check(!has(Serial.out, "Invalid cmd"), "a partial line is held, not rejected");
  Serial.feed("\n");
  tick(5);
  check(!has(Serial.out, "Invalid cmd"), "the completed line parses");

  // --- an over-long line is dropped whole, never split into two commands
  Serial.out.clear();
  Serial.feed(std::string(200, 'x') + "\n");
  tick(5);
  check(has(Serial.out, "line too long"), "an over-long line is reported and dropped");

  // --- light convention
  run(300, 1811, 1000);                                 // manual, armed
  check(g_pin_state[YELLOW_LIGHT_PIN] == HIGH, "manual armed = yellow");
  Serial.feed("MODE AUTO\n");
  run(300, 1811, 1811);
  check(g_pin_state[GREEN_LIGHT_PIN] == HIGH, "autonomous armed = green");
  run(300, 200, 1811);                                  // disarm
  check(g_pin_state[RED_LIGHT_PIN] == HIGH, "disarmed = red");

  // =====================================================================
  //  v5 fix 1: SBUS silent in AUTONOMOUS -> ESTOP + relay open < 500 ms
  // =====================================================================
  printf("\n-- SBUS silent while AUTONOMOUS --\n");
  getAutonomous();
  st = lastStateLine();
  check(has(st, " mode=3 ") && has(st, " armed=1") && !relayOpen(),
        "precondition: armed, AUTONOMOUS, relay closed");
  check(thruster_left.last != PWM_NEUTRAL_US, "precondition: the Jetson is driving the thrusters");

  unsigned long last_frame = g_millis;     // run() fed its last frame just now
  unsigned long opened_at = 0;
  // The Jetson keeps sending motor commands throughout: a live serial link
  // must NOT keep the boat going when the transmitter is gone.
  for (unsigned long t = 0; t < 1000; t++) {
    if ((t % 50) == 0) Serial.feed("0.5,0.5\n");
    tick(1);
    if (!opened_at && relayOpen()) opened_at = g_millis;
  }
  unsigned long reaction = opened_at ? opened_at - last_frame : 99999;
  printf("        relay opened %lu ms after the last SBUS frame\n", reaction);
  check(opened_at != 0, "the relay opens when SBUS goes silent in AUTONOMOUS (v4: never)");
  check(reaction < 500, "...in under 500 ms");
  st = lastStateLine();
  check(has(st, " mode=1 ") && has(st, " armed=0"), "STATE reports ESTOP, disarmed");
  check(thrustersNeutral(), "thrusters at neutral, motor commands ignored");
  check(has(Serial.out, "[E-STOP] RC link lost: no SBUS frame"), "the cause is logged");
  check(has(st, " wantauto=0"), "the Jetson's AUTO request is withdrawn");

  // =====================================================================
  //  v5 fix 2: no automatic re-arm after a failsafe (Ch7 must be cycled)
  // =====================================================================
  printf("\n-- link comes back with Ch7 still high --\n");
  Serial.feed("MODE AUTO\n");                // the Jetson insists; must not matter
  run(3000, 1811, 1811, "0.5,0.5");
  st = lastStateLine();
  check(has(st, " armed=0") && relayOpen(), "frames back, Ch7 high: stays disarmed, relay open (v4: re-armed)");
  check(has(st, " rclatch=1"), "rclatch=1 says why");
  check(thrustersNeutral(), "thrusters stay neutral");

  run(300, 200, 1811);                       // operator: Ch7 low
  st = lastStateLine();
  check(has(st, " rclatch=0") && has(st, " armed=0"), "Ch7 low on a live link clears the latch (still disarmed)");
  run(400, 1811, 1811);                      // operator: Ch7 high
  st = lastStateLine();
  check(has(st, " armed=1") && !relayOpen(), "Ch7 high again arms");
  check(has(st, " mode=2 ") && has(st, " wantauto=0"),
        "...in MANUAL: the AUTO sent during the outage was refused");
  check(has(Serial.out, "[NAK] MODE AUTO refused"), "the refusal is said on the serial link");
  Serial.feed("MODE AUTO\n");
  run(600, 1811, 1811);
  check(has(lastStateLine(), " mode=3 "), "a fresh MODE AUTO after re-arming is honoured");

  printf("\n-- receiver-declared failsafe (frames keep coming, failsafe bit set) --\n");
  Serial.feed("MODE AUTO\n");
  run(2600, 1811, 1811, "0.5,0.5");
  check(has(lastStateLine(), " mode=3 "), "precondition: AUTONOMOUS again");
  run(300, 1811, 1811, "0.5,0.5", 0x08);     // failsafe bit
  st = lastStateLine();
  check(has(st, " mode=1 ") && relayOpen(), "failsafe bit: ESTOP, relay open");
  run(300, 200, 1811, "0.5,0.5", 0x08);      // Ch7 low WHILE in failsafe: must not count
  run(1000, 1811, 1811, "0.5,0.5");          // link back, Ch7 high
  st = lastStateLine();
  check(has(st, " armed=0") && has(st, " rclatch=1"),
        "Ch7 low during the failsafe does not count; still latched after it");
  run(300, 200, 1811);
  run(400, 1811, 1811);
  check(has(lastStateLine(), " armed=1"), "Ch7 cycled on a live link re-arms");

  printf("\n-- SBUS silent in MANUAL (v4 already stopped here; still does) --\n");
  run(500, 1811, 1000);
  check(has(lastStateLine(), " mode=2 ") && !relayOpen(), "precondition: MANUAL armed");
  last_frame = g_millis; opened_at = 0;
  for (unsigned long t = 0; t < 1000; t++) { tick(1); if (!opened_at && relayOpen()) opened_at = g_millis; }
  check(opened_at && opened_at - last_frame < 500, "MANUAL: relay open in under 500 ms");
  run(200, 200, 1000); run(400, 1811, 1000);

  // =====================================================================
  //  v5 fix 3: Serial output never blocks
  // =====================================================================
  printf("\n-- serial output with a host that stops reading --\n");
  check(Serial.direct_prints == 0, "no direct Serial.print/println anywhere (all output is queued)");
  check(Serial.oversize_writes == 0, "no write() larger than availableForWrite() so far");

  Serial.feed("MODE AUTO\n");
  run(2600, 1811, 1811, "0.5,0.5");
  check(has(lastStateLine(), " mode=3 "), "precondition: AUTONOMOUS");
  Serial.out.clear();
  Serial.write_room = 0;                     // port open, host not reading
  t0 = g_millis;
  run(2000, 1811, 1811, "0.5,0.5");          // two seconds of a stuck host
  check(g_millis - t0 == 2000, "the loop keeps running while the host is stuck");
  check(Serial.oversize_writes == 0, "nothing tried to push bytes TinyUSB could not take");
  check(Serial.out.empty(), "nothing was sent (nothing could be)");
  check(tx_dropped > 0, "lines that did not fit were dropped and counted");
  check(thruster_left.last != PWM_NEUTRAL_US, "AUTONOMOUS still drives meanwhile");

  // Safety while the host is stuck: SBUS silence must still stop the boat.
  last_frame = g_millis; opened_at = 0;
  for (unsigned long t = 0; t < 1000; t++) {
    if ((t % 50) == 0) Serial.feed("0.5,0.5\n");
    tick(1);
    if (!opened_at && relayOpen()) opened_at = g_millis;
  }
  check(opened_at && opened_at - last_frame < 500, "with the host stuck, SBUS silence still opens the relay < 500 ms");

  // Host resumes, a few bytes at a time: whole lines come out, none cut.
  Serial.out.clear();
  for (int i = 0; i < 300; i++) { Serial.write_room = 24; feedSbus(200, 1811); tick(10); }
  Serial.write_room = LONG_MAX / 2;
  run(600, 200, 1811);
  st = lastStateLine();
  check(!st.empty() && has(st, " txdrop="), "STATE lines flow again once the host reads");
  check(has(st, " txdrop=") && !has(st, " txdrop=0"), "txdrop reports the lines lost while stuck");
  bool all_whole = true;
  size_t p = 0;
  while ((p = Serial.out.find("STATE ", p)) != std::string::npos) {
    size_t e = Serial.out.find('\n', p);
    if (e == std::string::npos) break;
    std::string line = Serial.out.substr(p, e - p);
    if (line.rfind("STATE ver=5 ", 0) != 0 || !has(line, " txdrop=")) all_whole = false;
    p = e;
  }
  check(all_whole, "every STATE line sent through a 24-byte window arrives whole");
  check(Serial.oversize_writes == 0, "still no oversize write()");

#if ESTOP_FEEDBACK_ENABLED
  // =====================================================================
  //  GPIO20 feedback (prepared, not wired on the boat)
  // =====================================================================
  printf("\n-- GPIO20 E-stop feedback --\n");
  run(200, 200, 1000); run(1000, 1811, 1000);
  check(has(lastStateLine(), " armed=1"), "precondition: MANUAL armed, rail present");
  g_pin_state[ESTOP_READ_PIN] = LOW;          // the rail collapsed
  run(300, 1811, 1000);
  st = lastStateLine();
  check(has(st, " armed=0") && has(st, " estoplatch=1") && relayOpen(),
        "rail LOW for > 100 ms disarms, latches, opens the relay");
  g_pin_state[ESTOP_READ_PIN] = HIGH;
  run(500, 1811, 1000);
  check(has(lastStateLine(), " armed=0"), "stays disarmed when the rail comes back");
  run(200, 200, 1000); run(1000, 1811, 1000);
  check(has(lastStateLine(), " armed=1"), "re-arms after Ch7 is cycled");
#endif

  printf("\n%s (%d failure(s))\n", failures ? "FAILED" : "ALL PASS", failures);
  return failures ? 1 : 0;
}
