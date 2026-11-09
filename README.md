# Arduino function generator

A primitive square-wave generator for an Arduino Uno: set frequency and duty
cycle from a desktop GUI, output on **pin 9** (reference to GND).

- `firmware/fgen/` — sketch using Timer1 hardware PWM (glitch-free, no jitter)
- `gui/fgen_gui.py` — Tk GUI talking to the board over USB serial

## Range

| | |
|---|---|
| Frequency | 0.25 Hz – 1 MHz (GUI limit; the timer can go to 8 MHz) |
| Duty | 0 – 100 % |
| Level | 0 V / 5 V, ~20 mA max |

The timer divides a 16 MHz clock, so not every frequency is reachable exactly.
The GUI shows the *actual* frequency and duty the board produces. Duty-cycle
resolution drops as frequency rises (for example, 16 steps at 1 MHz and 16000 at 1 kHz).

## Quick start

```sh
cd ~/Work/arduino
./fgen.sh          # open the GUI (fixes port permissions if needed)
./fgen.sh flash    # upload the firmware first, then open the GUI
```

Close the GUI window before running `./fgen.sh flash`: only one program can
use the Arduino's port at a time.

## Manual setup

```sh
# serial port access (log out and back in afterwards)
sudo usermod -aG uucp $USER

# flash the firmware
mise x arduino-cli@latest -- arduino-cli core install arduino:avr
mise x arduino-cli@latest -- arduino-cli compile --upload -p /dev/ttyACM0 --fqbn arduino:avr:uno firmware/fgen

# run the GUI
uv run gui/fgen_gui.py
```

## Serial protocol

115200 baud, newline-terminated: `F<hz>`, `D<percent>`, `E0`/`E1`, `?`.
Each command replies `OK F=<hz> D=<pct> P=<prescaler> T=<top> E=<0|1>`.
You can also drive it from any serial terminal or script.
