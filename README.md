# kontrol-leds

Set your own colours on the LEDs of **Native Instruments keyboards**: the light
guide above each key, the button lights and the touch strip. It talks to the
keyboard directly over USB HID, so it works without Komplete Kontrol or a DAW.

Device support comes from **profiles**, which are JSON files describing each
product. To add a keyboard, or a new version of one, you write a profile, not
code. See [docs/adding-a-device.md](docs/adding-a-device.md).

## Supported devices

| Model (`--model`) | Device | Status |
|---|---|---|
| `s88-mk2` | Komplete Kontrol S88 MK2 | **verified**: keys, 42 buttons, 25-dot touch strip |
| `s49-mk2`, `s61-mk2` | Komplete Kontrol S49 / S61 MK2 | unverified (same profile as the S88) |
| `s25-mk1` … `s88-mk1` | Komplete Kontrol S-Series MK1 | unverified: RGB light guide only |

Run `kkleds devices` for the live list. The keyboard is detected automatically
when it's plugged in. Use `--model` to pick a profile yourself.

## Setup

```powershell
cd $HOME\Documents\kontrol-s88-leds
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[midi,dev]"     # midi = optional, for `kkleds follow`
kkleds list                      # what's connected, and which profile it uses
```

Add `--dry-run` to any command to draw the result in the terminal instead of
sending it to the keyboard.

## Light guide

```powershell
kkleds colors                                   # the palette: 17 hues x (dim, medium, bright, pastel)
kkleds fill blue
kkleds fill red:0                               # dim (levels 0-2; plain "red" = 2)
kkleds fill red:pastel                          # the whitish tint
kkleds off

kkleds set A0-B2=blue C3-B5=green C6-C8=orange  # keyboard split
kkleds set C=white --base plum:0                # every C white on a dim plum background
kkleds set "C4,E4,G4=yellow"                    # a chord shape

kkleds scale D dorian                           # scale notes white, roots red
kkleds scale A minor_pentatonic --color cyan --root-color orange --keys C3-C6
kkleds gradient red yellow blue
kkleds piano --white white:0 --black violet

kkleds animate rainbow --speed 0.5              # also: chase, breathe, sparkle
kkleds follow --velocity --sustain --meter      # light keys as you play
```

### Colours

You can give a colour as:
- `off`
- a hue name: `red orange light_orange warm_yellow yellow lime green mint cyan
  turquoise blue plum violet purple magenta fuchsia white`
- a hue with a level: `:0`/`:dim`, `:1`/`:medium`, `:2`/`:bright` (the default)
  or `:pastel`
- true RGB: `#rrggbb`. Palette devices snap it to the nearest palette colour,
  and RGB devices show it exactly.
- a raw palette byte, like `0x2B`

### Key selectors

`all`, `white`, `black`, a pitch class (`C`, `F#` = every one on the keyboard), a
note (`C4`, `60`), a range (`A0-C4`, `C4..C5`), or a comma list (`"C,E,G"`).
Uses C4 = MIDI 60.

## Buttons and touch strip

```powershell
kkleds buttons --list                          # names, slots, groups for your device
kkleds buttons play=on rec=red stop=dim        # single-colour buttons: on / medium / dim / off
kkleds buttons display=blue mute=red encoder=cyan
kkleds buttons all=dim transport=on --base off
kkleds strip rainbow
kkleds strip blue green orange                 # gradient
kkleds strip green --bar 60                    # level-meter style
kkleds animate rainbow --strip                 # mirror an effect on the strip
```

The last button state is saved per device in `~/.kkleds/state/`, so separate
commands don't wipe each other. Add `--reset` to start from all off.

## Presets

A preset is a list of light guide layers, plus an optional `buttons` section:

```json
{
  "layers": [
    { "fill": "off" },
    { "keys": "A0-B2", "color": "blue" },
    { "keys": "C3-C8", "gradient": ["green", "yellow"] },
    { "scale": "C major", "color": "white:1", "root": "red", "other": "off" }
  ],
  "buttons": { "all": "dim", "transport": "on", "display": "blue:0", "strip": ["blue", "orange"] }
}
```

```powershell
kkleds preset presets\split-keyboard.json
```

Presets don't depend on a particular device. Keys outside a smaller keyboard's
range are skipped, and a device without button LEDs skips the `buttons` section.

## If the colours don't stick

The NI background services (`NIHardwareService`, `NIHostIntegrationAgent`) also
control the LEDs, especially while Komplete Kontrol or a DAW integration is
running. Options:

1. Add `--hold` to keep re-sending until Ctrl+C.
2. Close Komplete Kontrol and your DAW.
3. From an **admin** PowerShell, run `.\scripts\ni-services.ps1 stop`, then
   `start` when you're done.

## Adding devices

```powershell
kkleds list                                        # find the product ID
kkleds profile-new --pid 0x1730 --key a49-mk1 --keys 49
kkleds buttons-probe --model a49-mk1 --save $HOME\.kkleds\profiles\a49-mk1.json
kkleds profile-check $HOME\.kkleds\profiles\a49-mk1.json
```

Full walkthrough and format: [docs/adding-a-device.md](docs/adding-a-device.md).
S MK2 hardware notes: [docs/komplete-kontrol-s-mk2.md](docs/komplete-kontrol-s-mk2.md).

## Development

```powershell
pytest
python -m kkleds --help
```

| Module | What it does |
|---|---|
| `profile.py` | loads, merges and validates profiles; registry and USB matching |
| `profiles/*.json` | built-in device profiles |
| `encodings.py` | colour formats on the wire: palette, rgb, mono |
| `colors.py` | NI palette, RGB values, parsing, gradients |
| `notes.py` | note names, scales, key selectors |
| `layout.py` | `Frame`: light guide painting and presets |
| `buttons.py` | `ButtonFrame`: button and strip painting, saved state |
| `device.py` | HID access and terminal dry-run, all driven by the profile |
| `effects.py` | animations |
| `cli.py` | the `kkleds` command |
