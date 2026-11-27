# kontrol-s88-leds

Set your own colours on the **Native Instruments Komplete Kontrol S88 MK2** light
guide (the LED above each key), and experiment with the button LEDs. It talks to
the keyboard directly over USB HID, so it works without Komplete Kontrol or a DAW.

It also has partial support for the S49 / S61 MK2 (`--model s49|s61`).

## Setup

```powershell
cd $HOME\Documents\kontrol-s88-leds
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[midi,dev]"     # midi = optional, for `kkleds follow`
```

Check that the keyboard is detected:

```powershell
kkleds list
```

Add `--dry-run` to any command to draw the result in the terminal instead of
sending it to the keyboard.

## Examples

```powershell
kkleds colors                                   # show the 17-hue x 4-brightness palette
kkleds fill blue                                # all keys blue
kkleds fill red:0                               # dim red (levels 0-2; plain "red" = 2)
kkleds fill red:pastel                          # the whitish tint (red:pastel looks pink)
kkleds off

kkleds set A0-B2=blue C3-B5=green C6-C8=orange  # keyboard split
kkleds set C=white --base plum:0                # every C white on a dim plum background
kkleds set "C4,E4,G4=yellow"                    # a chord shape

kkleds scale D dorian                           # scale notes white, roots red
kkleds scale A minor_pentatonic --color cyan --root-color orange --keys C3-C6
kkleds gradient red yellow blue                 # blend across the keyboard
kkleds piano --white white:0 --black violet

kkleds preset presets\split-keyboard.json       # JSON layer presets

kkleds animate rainbow --speed 0.5              # also: chase, breathe, sparkle
kkleds animate sparkle --color cyan --fps 40

kkleds follow                                   # light keys as you play
kkleds follow --velocity --sustain --base presets\c-major-guide.json
```

### Colours

`off`, a hue name (`red orange light_orange warm_yellow yellow lime green mint
cyan turquoise blue plum violet purple magenta fuchsia white`) with an optional
level: `:0`/`:dim`, `:1`/`:medium`, `:2`/`:bright` (the default) or
`:pastel` (a whitish tint). You can also use `#rrggbb` (snapped to the nearest
palette colour) or a raw byte like `0x2B`. The keyboard has a fixed palette of
17 hues x 4 levels, so it can't show arbitrary RGB.

### Key selectors

`all`, `white`, `black`, a pitch class (`C`, `F#` = every one on the keyboard), a
note (`C4`, `60`), a range (`A0-C4`, `C4..C5`), or a comma list (`"C,E,G"`).
Uses C4 = MIDI 60, so the S88 spans A0-C8.

### Presets

A preset is a list of layers, applied in order:

```json
{
  "layers": [
    { "fill": "off" },
    { "keys": "A0-B2", "color": "blue:2" },
    { "keys": "C3-C8", "gradient": ["green", "yellow"] },
    { "scale": "C major", "color": "white:1", "root": "red", "other": "off" },
    { "keys": "black", "color": "plum:0" }
  ]
}
```

### Buttons and touch strip

```powershell
kkleds buttons --list                          # every name, slot and group
kkleds buttons play=on rec=red stop=dim        # single-colour buttons: on / medium / dim / off
kkleds buttons display=blue mute=red solo=yellow encoder=cyan
kkleds buttons all=dim transport=on --base off # groups: all, display, encoder, transport,
                                               #   preset, browse, edit, color, mono, strip
kkleds strip rainbow                           # 25 colour dots
kkleds strip blue green orange                 # gradient
kkleds strip green --bar 60                    # level-meter style
kkleds animate rainbow --strip                 # mirror an effect on the strip
kkleds follow --meter                          # strip shows each note's velocity
```

Mute, Solo, display buttons 1-8, the 4-D encoder arrows and the touch strip
take any colour. Every other button is single-colour and uses only the level.
The octave lights are driven by the keyboard itself.

The last button state is saved in `~/.kkleds/buttons-state.json`, so
`kkleds strip ...` won't wipe the buttons you set before. Add `--reset` to start
from all off.

Presets can include buttons as well:

```json
"buttons": { "all": "dim", "transport": "on", "display": "blue:0", "strip": ["blue", "orange"] }
```

## If the colours don't stick

The NI background services (`NIHardwareService`, `NIHostIntegrationAgent`) also
control the light guide, especially while Komplete Kontrol or a DAW integration
is running. Options:

1. Add `--hold` to keep re-sending your colours until Ctrl+C.
2. Close Komplete Kontrol and your DAW.
3. Stop the services while you use kkleds, from an **admin** PowerShell:
   `.\scripts\ni-services.ps1 stop`, then `start` when you're done. DAW
   integration won't work while they're stopped.

## Hardware status (S88 MK2, Windows 11)

| Feature | Status |
|---|---|
| Light guide init (`A0 00 00`) | confirmed, required |
| Per-key colours, key order, hue order | confirmed |
| Levels 0-2 + pastel | confirmed |
| Animations at 30 fps | confirmed, smooth |
| `follow` (velocity colours, sustain) | confirmed |
| Button LEDs (report `0x80`) | fully mapped: 42 buttons, colour vs single-colour |
| Touch strip (25 colour dots) | confirmed, incl. velocity meter |
| Octave -/+ lights | not controllable (firmware) |

See [docs/protocol.md](docs/protocol.md) for the byte-level details.

## Development

```powershell
pytest
python -m kkleds --help
```

Layout: `src/kkleds/` has `protocol.py` (report IDs), `colors.py` (palette),
`notes.py` (note names, scales, selectors), `layout.py` (models, `Frame`),
`device.py` (HID and dry-run output), `effects.py` (animations) and `cli.py`.
