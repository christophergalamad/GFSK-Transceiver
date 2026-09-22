#!/bin/bash
# Stop the GALAMAD GFSK Transceiver
pkill -f gfsk_ui.py 2>/dev/null
pkill -f hackrf_transfer 2>/dev/null
echo "Stopped"
