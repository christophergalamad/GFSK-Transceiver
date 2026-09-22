#!/bin/bash
# Start the GALAMAD GFSK Transceiver
cd "$(dirname "$0")"
setsid nohup python3 gfsk_ui.py > ui.log 2>&1 < /dev/null &
echo "Started (PID $!) — open http://localhost:5000"
