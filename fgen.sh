#!/usr/bin/env bash
# Start the Arduino function generator.
#
#   ./fgen.sh          fix port permissions if needed, then open the GUI
#   ./fgen.sh flash    same, but upload the firmware to the Arduino first
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

# 1. Find the Arduino
port=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | head -n1 || true)
if [[ -z "$port" ]]; then
    echo "No Arduino found. Plug it in via USB and try again." >&2
    exit 1
fi
echo "Arduino found on $port"

# 2. Make sure we are allowed to use it
if [[ ! -r "$port" || ! -w "$port" ]]; then
    echo "No permission to use $port, fixing it (sudo will ask for your password)..."
    sudo chmod a+rw "$port"
fi

# 3. Upload the firmware if asked
if [[ "${1:-}" == "flash" ]]; then
    echo "Uploading firmware..."
    arduino_cli() { mise x arduino-cli@latest -- arduino-cli "$@"; }
    arduino_cli core list | grep -q '^arduino:avr' || arduino_cli core install arduino:avr
    arduino_cli compile --upload -p "$port" --fqbn arduino:avr:uno firmware/fgen
    echo "Firmware uploaded."
fi

# 4. Open the GUI
echo "Opening the GUI... (close the window to quit)"
exec uv run gui/fgen_gui.py
