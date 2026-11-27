# Komplete Kontrol S-Series MK2: LED protocol notes

These are the hardware findings behind
`src/kkleds/profiles/komplete-kontrol-s-mk2.json`. Everything here was tested
on an **S88 MK2**. The S49 and S61 MK2 share the profile but are unverified:
they probably match, but nobody has tested them yet.

## USB identity (confirmed on this PC)

Windows Device Manager lists the S88 MK2 as a composite USB device:

| Interface | What it is |
|-----------|------------|
| `USB\VID_17CC&PID_1630&MI_00` | NI USB device (audio/MIDI, NI driver) |
| `USB\VID_17CC&PID_1630&MI_02` | **HID, vendor-defined.** The LEDs are on this one |
| `USB\VID_17CC&PID_1630&MI_03` | NI "BD" interface (the screens) |
| `USB\VID_17CC&PID_1630&MI_04` | DFU (firmware update) |

- Vendor ID `0x17CC` (Native Instruments)
- Product IDs: S49 MK2 `0x1610`, S61 MK2 `0x1620`, **S88 MK2 `0x1630`**

## Init: HID output report `0xA0` (confirmed)

```
[0xA0] [0x00] [0x00]
```

This switches the keyboard into light guide mode. **Without it, report `0x81`
is ignored.** `kkleds` sends it automatically every time it opens the device.
SynthesiaKontrol does the same.

## Light guide: HID output report `0x81` (confirmed on S88 MK2)

```
[0x81] [key 0] [key 1] ... [key 87]
```

There is one byte per key, starting at the lowest key (A0 on the S88). The colour
always follows the **physical key**. The octave and transpose buttons change the
MIDI notes, not the lights.

Each byte is an index into NI's colour palette, the same one Maschine MK3 uses:

```
byte = (hue << 2) | level      0x00 = off
hue    1..16 = red, orange, light orange, warm yellow, yellow, lime, green,
               mint, cyan, turquoise, blue, plum, violet, purple, magenta,
               fuchsia
       17    = white
level  0, 1, 2 = dim, medium, bright
       3       = pastel: a whitish tint of the hue (red:3 looks pink)
```

Tested on hardware: the hue order is correct (a 17-group sweep showed the
expected rainbow ending in white), and red levels 0-2 got brighter while level
3 was pink. Updating at 30 fps animates smoothly.

## Button LEDs + touch strip: HID output report `0x80` (mapped on S88 MK2)

```
[0x80] [slot 0] [slot 1] ... [slot 68]
```

Every slot takes a **palette colour byte**, the same format as the light guide.

| Slots | LEDs | Kind |
|---|---|---|
| 0, 1 | Mute, Solo | colour |
| 2-9 | Display buttons 1-8 (above the screens) | colour |
| 10-13 | 4-D encoder arrows: left, up, down, right | colour |
| 14-41 | shift, scale_edit, arp_edit, scene, undo_redo, quantize, auto, pattern, preset_up, track, loop, metro, tempo, preset_down, key_mode, play, rec, stop, preset_left, preset_right, clear, browser, plugin, mixer, instance, midi, setup, fixed_vel (in that order) | single colour |
| 42, 43 | nothing visible | - |
| 44-68 | Touch strip, 25 dots (44 = first dot, 68 = last) | colour |
| 69+ | nothing visible | - |

How we tested it:
- Single-colour LEDs ignore the hue and show only the level bits: level 0 is dim,
  1 is medium, 2 is bright, and 3 looks bright too. Play/Rec/Stop at levels 0/1/2
  came out dim < medium < bright.
- Hue 0 (bytes 1-3) is off, the same as on the light guide. Bytes above `0x47`
  have no valid hue: they light colour LEDs white and single-colour LEDs bright.
- The **Octave -/+** lights are not in this report (the firmware drives them),
  and the 8 knobs have no LEDs.

`kkleds buttons --list` prints the whole map. `buttons-probe` is only needed for
other models.

## Windows notes

- On Windows, hidapi pads a short report up to the interface's longest output
  report, so sending fewer bytes than the full report is safe.
- `NIHardwareService` / `NIHostIntegrationAgent` also write to the light guide,
  for example to show key zones and scale highlights when Komplete Kontrol is
  loaded. If they overwrite your colours, use `--hold` or stop them with
  `scripts\ni-services.ps1 stop` (admin).
