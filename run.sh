#!/usr/bin/env bash
# Starts the display. Use:  bash run.sh --setup    (first time: where you watch from)
#                           bash run.sh            (live, full screen)
#                           bash run.sh --demo     (simulated traffic)
#                           bash run.sh --check    (test the set-up)
# It uses an existing Python with pygame if it can find one, and only builds one if not.
cd "$(dirname "$0")" || exit 1

for candidate in ./venv ../retro-adsb-radar/venv "$HOME/retro-adsb-radar/venv"; do
    if [ -x "$candidate/bin/python" ] && "$candidate/bin/python" -c "import pygame" >/dev/null 2>&1; then
        exec "$candidate/bin/python" main.py "$@"
    fi
done

if python3 -c "import pygame" >/dev/null 2>&1; then
    exec python3 main.py "$@"
fi

echo "pygame is not installed. On Raspberry Pi OS the quickest fix is:"
echo "    sudo apt install python3-pygame python3-requests"
echo "Trying to build a private Python environment in ./venv instead (one-off, needs internet)..."
python3 -m venv venv || { echo "Could not create it. Try: sudo apt install python3-venv"; exit 1; }
./venv/bin/pip install --quiet -r requirements.txt || { echo "Installing pygame failed. Use the apt command above."; rm -rf venv; exit 1; }
exec ./venv/bin/python main.py "$@"
