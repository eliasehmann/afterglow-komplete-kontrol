# Changelog

## 1.1.0

- **New name: Afterglow** (was Kontrol LEDs). The app is now `Afterglow.exe`.
  "Start with Windows" carries over from the old name.
- **Website version:** the same app in Chrome or Edge, with nothing to
  install. It talks to the keyboard over WebHID and to play-along over
  Web MIDI, runs the real Python engine in the page (Pyodide), and stores your
  looks in the browser. Build it with `python webapp\build.py`; see
  `docs/website.md`.
- The page's API now lives in one place (`api.dispatch`), shared by the app's
  local server and the website.
- **Light up what I play** now lights pressed keys plain white instead of
  colouring them by velocity.

## 1.0.0

The first release for everyone, as a Windows app: download, double-click, done.

- **Desktop app** (`KontrolLEDs.exe`). It runs in the system tray, with Open,
  Turn lights off, Start with Windows and Quit. Opening it a second time just
  shows the running app.
- **Scenes:** 14 animated moods with live previews on every tile, plus speed
  and brightness controls. Dithering gives smooth fades, even though the
  keyboard only has 3 brightness levels per colour.
- **Custom scenes:** your colours and a motion, saved as your own scenes.
- **Paint your own:** colour every key, touch strip light and button on a
  drawing laid out like the real keyboard. White-only buttons are shown
  honestly.
- **Scale guide:** light up the notes of any scale in any key.
- **Light up what I play:** keys light up as you play, coloured by velocity
  and held by the sustain pedal, on top of any scene or look.
- **Remembers your last look** and brings it back at the next start.
- **Reconnects by itself** when the keyboard is unplugged or switched off.
- Plain-language messages when the keyboard can't be found.

## 0.2.0

- Device support moved into JSON profiles (USB IDs, reports, button map,
  colour encoding), with per-model variants. Unverified S MK1 profile.
- Full button and touch strip map for the S88 MK2.

## 0.1.0

- First version: `kkleds` command line for the S88 MK2 light guide.
