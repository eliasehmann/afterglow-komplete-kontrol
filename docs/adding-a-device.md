# Adding a device

All device support is data. To add a new product, or a new version of one, you
write a **profile**: a JSON file that describes the device. You don't need to
change any code.

## 1. Find the device

```powershell
kkleds list
```

This lists every Native Instruments HID interface, with its USB IDs and the
profile it matches. A device that's missing says `no profile yet` and shows its
product ID.

## 2. Create a starter profile

```powershell
kkleds profile-new --pid 0x1730 --key a49-mk1 --name "Komplete Kontrol A49" --keys 49 --lowest-note 36
```

This writes `~/.kkleds/profiles/a49-mk1.json`. Profiles in that folder load
automatically. You can also load any file with `--profile FILE`. The starter
copies the S MK2 report numbers, so treat every value in it as a guess until
you've tested it.

## 3. Work out the light guide (if it has one)

```powershell
kkleds fill red --model a49-mk1          # do the keys light up? all of them?
kkleds palette --model a49-mk1           # palette devices: is the hue order right?
kkleds raw 81 04 08 0c --model a49-mk1   # try raw reports when nothing works
```

Things to check:
- **Nothing lights up:** check the `init` report, `light_guide.report` and
  `hid.interface`. `kkleds list` shows the interface numbers.
- **The wrong keys light up:** fix `keys` and `lowest_note`.
- **Wrong colours on an RGB device:** change `encoding.order`, e.g. `"grb"`.

## 4. Map the buttons (if it has button LEDs)

```powershell
kkleds buttons-probe --model a49-mk1 --save $HOME\.kkleds\profiles\a49-mk1.json
```

This lights one slot at a time. Type a name for each LED that lights up, or
press Enter to skip. The names go straight into the profile's `buttons.leds`.
After that:

1. Add a `strip` entry if the device has a touch strip.
2. Add `groups` for families of buttons.
3. Add `color` to list which LEDs show colour. Find out by sending
   `kkleds buttons all=blue` and seeing which ones actually turn blue.

## 5. Check it and share it

```powershell
kkleds profile-check $HOME\.kkleds\profiles\a49-mk1.json
pytest
```

When it works, move the file into `src/kkleds/profiles/` and set `"status"`.
Each built-in profile is tested for loading, validation and unique IDs.

---

## Profile format

```jsonc
{
  "id": "komplete-kontrol-s-mk2",          // family id
  "name": "Komplete Kontrol S-Series MK2",
  "vendor_id": "0x17CC",                   // numbers: int or "0x.." strings
  "status": "unverified",                  // verified | partial | unverified
  "source": "where the knowledge came from",
  "notes": "free text shown to humans",

  "hid": { "interface": 2, "usage_page": null },  // which HID interface to open
  "init": [["0xA0", "0x00", "0x00"]],             // reports sent right after opening

  "light_guide": {
    "report": "0x81",
    "encoding": "palette",                 // or {"type": "rgb", "order": "rgb", "max": 255}
    "keys": 88,                            //    or {"type": "mono", "max": 127}
    "lowest_note": 21                      // MIDI note of the leftmost key (C4 = 60)
  },

  "buttons": {
    "report": "0x80",
    "size": 69,                            // number of LED slots in the report
    "encoding": "palette",
    "leds": { "play": 29, "rec": 30 },     // name -> slot
    "strip": { "first": 44, "length": 25 },// adds strip_1..strip_25 and group "strip"
    "aliases": { "record": "rec" },
    "groups": { "transport": ["play", "rec"] },   // members: names, other groups, slot numbers
    "color": ["mute", "solo", "strip"]     // LEDs that show colour; others are level-only
  },

  "variants": {                            // one entry per USB product ID
    "s88-mk2": {                           // the --model name; must be unique
      "name": "Komplete Kontrol S88 MK2",
      "product_id": "0x1630",
      "status": "verified",
      "light_guide": { "keys": 88, "lowest_note": 21 }
    }
  }
}
```

(JSON has no comments. Leave out the `//` notes in real files.)

### How variants work

A variant inherits everything from the family. Any key it sets replaces the
family's, and objects are merged deeply. For example, a variant can change only
`light_guide.keys`, or give itself a whole different `buttons` section. A file
with no `variants` is a single device, using its `id` as the model name.

### Groups you get for free

- `all`: every named LED except the strip
- `everything`: all LEDs, including the strip
- `strip`: the touch strip dots
- `color` (also `colour`, `rgb`) and `mono`: added when the profile lists `color`

### Encodings

| type | bytes per LED | what it does |
|---|---|---|
| `palette` | 1 | NI palette index (S MK2). RGB input snaps to the nearest entry. |
| `rgb` | 3 | true colour in `order` (`rgb`, `grb`…), scaled to `0..max` (S MK1) |
| `mono` | 1 | brightness `0..max`, for single-colour LEDs |

New formats go in `src/kkleds/encodings.py`: subclass `Encoding`, then add it to
`TYPES`.
