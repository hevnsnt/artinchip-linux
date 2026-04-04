#!/bin/bash
# Configure the tinyscreen EVDI display position.
# Run this after the EVDI bridge daemon is started.

# Find the EVDI output name (usually DVI-I-1-1 or similar)
# Excludes known physical outputs: eDP (laptop), HDMI, DP
EVDI_OUTPUT=$(xrandr | grep " connected" | grep -v "eDP\|HDMI\|DP-" | head -1 | cut -d' ' -f1)

if [ -z "$EVDI_OUTPUT" ]; then
    echo "ERROR: No EVDI output found. Is tinyscreen-evdi running?"
    exit 1
fi

echo "Found EVDI output: $EVDI_OUTPUT"

# Get primary display name
PRIMARY=$(xrandr | grep " primary" | cut -d' ' -f1)
echo "Primary display: $PRIMARY"

# Position the tinyscreen below the primary display, rotated 180°
xrandr --output "$EVDI_OUTPUT" \
    --mode 1920x440 \
    --rotate inverted \
    ${PRIMARY:+--below "$PRIMARY"}

echo "Display configured: $EVDI_OUTPUT at 1920x440 (inverted) below $PRIMARY"
