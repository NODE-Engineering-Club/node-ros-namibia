# Host-side firmware tests. No Pico, no Arduino toolchain, no hardware.
#
#   make -C firmware/test
#
# This compiles the sketch against a stub Arduino core (ArduinoStub.h) and
# drives it with synthetic SBUS frames and serial input. It cannot tell you
# anything about timing, electrical behaviour or the real USB stack. What it
# does tell you is whether the mode arbitration, the failsafes, the light
# convention and the STATE line still say what they are supposed to say --
# which is exactly the class of thing that broke silently between v3 and the
# Jetson and went unnoticed for months.

CXXFLAGS = -std=c++17 -Wall -Wextra -Wno-unused-parameter -I.

# The sketch under test. pico-node_v5 is the current firmware; pico-node_v4 is
# kept unchanged next to it to roll back to, and its own harness is in git
# history (the commit before Phase B c).
SKETCH ?= ../pico-node_v5/pico-node_v5.ino

all: run

sketch.inc: $(SKETCH)
	sed -e 's|#include <Servo.h>||' \
	    -e 's|#include <Adafruit_TinyUSB.h>||' \
	    -e '1i #include "ArduinoStub.h"' $< > $@

fwtest: harness.cpp ArduinoStub.h sketch.inc
	$(CXX) $(CXXFLAGS) -o $@ harness.cpp

# The same harness against the GPIO20 E-stop feedback path, which ships
# disabled (not wired). Builds it so the day it is enabled it is not untested.
fwtest_feedback: harness.cpp ArduinoStub.h sketch.inc
	$(CXX) $(CXXFLAGS) -DESTOP_FEEDBACK_ENABLED=1 -o $@ harness.cpp

# Dumps the full decision table for the Python cross-check to compare
# against asket_common.mode_arbitration. Asserts nothing itself.
table: arbitration_table.cpp ArduinoStub.h sketch.inc
	$(CXX) $(CXXFLAGS) -o fwtable arbitration_table.cpp

run: fwtest fwtest_feedback
	./fwtest
	./fwtest_feedback

clean:
	rm -f fwtest fwtest_feedback fwtable sketch.inc

.PHONY: all run clean table
