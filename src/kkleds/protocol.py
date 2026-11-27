"""Low-level constants for the Komplete Kontrol S-series MK2 USB HID interface.

Everything here was observed on Windows (Device Manager shows the S88 MK2 as
USB\\VID_17CC&PID_1630 with its HID interface on MI_02) or comes from community
reverse-engineering of the MK2 keyboards. Anything marked UNVERIFIED should be
confirmed with `kkleds palette` / `kkleds buttons-probe` on real hardware.
"""

NI_VENDOR_ID = 0x17CC

# USB interface number that carries the vendor-defined HID collection.
HID_INTERFACE = 2

# Must be sent once after opening, or the light guide ignores report 0x81
# (confirmed on an S88 MK2; same as ojacques/SynthesiaKontrol).
LIGHT_GUIDE_INIT = bytes([0xA0, 0x00, 0x00])

# HID output report that sets the light guide (one byte per key, lowest key first).
LIGHT_GUIDE_REPORT = 0x81

# HID output report that sets the button LEDs and touch strip (one palette
# colour byte per LED). Mapped on an S88 MK2 - see buttons.py.
BUTTON_REPORT = 0x80

# Colour bytes are 7-bit.
MAX_VALUE = 0x7F
