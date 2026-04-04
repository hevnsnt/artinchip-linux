#!/usr/bin/env python3
"""Generate a valid 128-byte EDID binary for the tinyscreen 1920x440 bar display."""

import struct
import sys


def generate_edid():
    """Build a minimal valid EDID 1.3 block for 1920x440 @ 60Hz."""
    edid = bytearray(128)

    # Bytes 0-7: Header
    edid[0:8] = b'\x00\xFF\xFF\xFF\xFF\xFF\xFF\x00'

    # Bytes 8-9: Manufacturer ID "TSC" (Tinyscreen)
    # EISA 3-letter code: T=20, S=19, C=3
    # Packed big-endian: (T << 10) | (S << 5) | C
    mfg = (20 << 10) | (19 << 5) | 3
    struct.pack_into('>H', edid, 8, mfg)

    # Bytes 10-11: Product code
    struct.pack_into('<H', edid, 10, 0x0440)

    # Bytes 12-15: Serial number
    struct.pack_into('<I', edid, 12, 1)

    # Byte 16: Week of manufacture
    edid[16] = 1
    # Byte 17: Year of manufacture (year - 1990)
    edid[17] = 2026 - 1990

    # Byte 18-19: EDID version 1.3
    edid[18] = 1
    edid[19] = 3

    # Byte 20: Video input (digital, DFP 1.x compatible)
    edid[20] = 0x80

    # Byte 21-22: Physical size in cm (37cm x 9cm approx)
    edid[21] = 37
    edid[22] = 9

    # Byte 23: Gamma (2.2 = value 120, stored as (gamma*100)-100)
    edid[23] = 120

    # Byte 24: Feature support (RGB color, preferred timing in DTD1)
    edid[24] = 0x0A

    # Bytes 25-34: Chromaticity coordinates (sRGB default values)
    edid[25:35] = bytes([0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54])

    # Bytes 35-37: Established timings (none)
    edid[35] = edid[36] = edid[37] = 0x00

    # Bytes 38-53: Standard timings (unused, fill with 0x0101)
    for i in range(38, 54, 2):
        edid[i] = 0x01
        edid[i + 1] = 0x01

    # Bytes 54-71: Detailed Timing Descriptor #1 (1920x440 @ 60Hz)
    #
    # H: 1920 active + 48 front + 32 sync + 80 back = 2080 total
    # V: 440 active + 3 front + 5 sync + 12 back = 460 total
    # Pixel clock = 2080 * 460 * 60 = 57,408,000 Hz = 57.41 MHz
    # Stored as pixel_clock / 10000
    pixel_clock = 5741  # 57.41 MHz in units of 10kHz

    h_active, h_blank = 1920, 160   # front(48) + sync(32) + back(80)
    v_active, v_blank = 440, 20     # front(3) + sync(5) + back(12)
    h_front, h_sync = 48, 32
    v_front, v_sync = 3, 5

    struct.pack_into('<H', edid, 54, pixel_clock)
    edid[56] = h_active & 0xFF
    edid[57] = h_blank & 0xFF
    edid[58] = ((h_active >> 8) & 0xF) << 4 | ((h_blank >> 8) & 0xF)
    edid[59] = v_active & 0xFF
    edid[60] = v_blank & 0xFF
    edid[61] = ((v_active >> 8) & 0xF) << 4 | ((v_blank >> 8) & 0xF)
    edid[62] = h_front & 0xFF
    edid[63] = h_sync & 0xFF
    edid[64] = ((v_front & 0xF) << 4) | (v_sync & 0xF)
    edid[65] = (((h_front >> 8) & 0x3) << 6) | (((h_sync >> 8) & 0x3) << 4) | \
               (((v_front >> 4) & 0x3) << 2) | ((v_sync >> 4) & 0x3)
    edid[66] = 370 & 0xFF   # H image size mm low
    edid[67] = 85 & 0xFF    # V image size mm low
    edid[68] = ((370 >> 8) & 0xF) << 4 | ((85 >> 8) & 0xF)
    edid[69] = 0  # H border
    edid[70] = 0  # V border
    edid[71] = 0x18  # non-interlaced, no stereo, digital separate sync

    # Bytes 72-89: Descriptor #2 -- Monitor name "TinyScreen"
    edid[72:75] = b'\x00\x00\x00'
    edid[75] = 0xFC  # Monitor name tag
    edid[76] = 0x00
    name_str = b'TinyScreen'
    edid[77:90] = name_str + b'\n' + b' ' * (13 - len(name_str) - 1)

    # Bytes 90-107: Descriptor #3 -- Monitor range limits
    edid[90:93] = b'\x00\x00\x00'
    edid[93] = 0xFD  # Range limits tag
    edid[94] = 0x00
    edid[95] = 56   # min V freq Hz
    edid[96] = 76   # max V freq Hz
    edid[97] = 30   # min H freq kHz
    edid[98] = 81   # max H freq kHz
    edid[99] = 6    # max pixel clock / 10 MHz (ceil(57.41/10) = 6 → 60 MHz)
    edid[100:108] = b'\x00' * 8

    # Bytes 108-125: Descriptor #4 -- unused
    edid[108:126] = b'\x00' * 18

    # Byte 126: Extension block count
    edid[126] = 0

    # Byte 127: Checksum (make all 128 bytes sum to 0 mod 256)
    edid[127] = (256 - (sum(edid[:127]) % 256)) % 256

    return bytes(edid)


if __name__ == '__main__':
    edid = generate_edid()
    assert len(edid) == 128
    assert sum(edid) % 256 == 0, "Bad checksum"
    print(f"EDID generated: {len(edid)} bytes, checksum valid")
    print(f"  Pixel clock: {struct.unpack_from('<H', edid, 54)[0] * 10 / 1000:.2f} MHz")

    out = sys.argv[1] if len(sys.argv) > 1 else '/opt/tinyscreen/edid_1920x440.bin'
    with open(out, 'wb') as f:
        f.write(edid)
    print(f"  Saved to {out}")
