"""Tests for the EDID binary generator."""
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from edid import generate_edid


def test_length_128_bytes():
    assert len(generate_edid()) == 128


def test_checksum_valid():
    edid = generate_edid()
    assert sum(edid) % 256 == 0, f"Bad checksum: remainder={sum(edid) % 256}"


def test_header_magic():
    edid = generate_edid()
    assert edid[0:8] == b'\x00\xFF\xFF\xFF\xFF\xFF\xFF\x00'


def test_manufacturer_is_tsc():
    """EISA 3-letter code: T=20, S=19, C=3, packed big-endian."""
    edid = generate_edid()
    mfg = struct.unpack_from('>H', edid, 8)[0]
    c1 = chr(((mfg >> 10) & 0x1F) + ord('A') - 1)
    c2 = chr(((mfg >> 5) & 0x1F) + ord('A') - 1)
    c3 = chr((mfg & 0x1F) + ord('A') - 1)
    assert c1 + c2 + c3 == 'TSC', f"Got '{c1}{c2}{c3}' (0x{mfg:04X})"


def test_resolution_1920x440():
    edid = generate_edid()
    h_active = edid[56] | ((edid[58] >> 4) << 8)
    v_active = edid[59] | ((edid[61] >> 4) << 8)
    assert (h_active, v_active) == (1920, 440), f"Got {h_active}x{v_active}"


def test_pixel_clock_approx_57mhz():
    edid = generate_edid()
    pixel_clock_10khz = struct.unpack_from('<H', edid, 54)[0]
    mhz = pixel_clock_10khz * 10 / 1000
    assert 55 <= mhz <= 60, f"Pixel clock {mhz:.2f} MHz out of range"


def test_monitor_name_tinyscreen():
    edid = generate_edid()
    assert edid[75] == 0xFC, f"Tag byte 0x{edid[75]:02X}, expected 0xFC"
    name = edid[77:90].split(b'\n')[0].decode('ascii')
    assert name == 'TinyScreen', f"Got '{name}'"


def test_edid_version_1_3():
    edid = generate_edid()
    assert (edid[18], edid[19]) == (1, 3), f"Got {edid[18]}.{edid[19]}"


if __name__ == '__main__':
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except AssertionError as e:
                print(f"  FAIL  {name}: {e}")
                failed += 1
            except Exception as e:
                print(f"  ERROR {name}: {type(e).__name__}: {e}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
