# The website version

The same Afterglow, in the browser, with nothing to install. It's a static
site: any web host with HTTPS works.

## How it works

- **The page** is the app's own `src/kkleds/web/index.html`.
  `webapp/kkleds-web.js` is added in front of it, and the build marks the page
  as the web version (`window.KKLEDS_WEB`).
- **Python in the browser:** the glue script loads Pyodide from the jsDelivr
  CDN and runs the real `kkleds` package (from `kkleds.zip`) in the browser.
  Scenes, dithering, profiles and saved looks are the same code as in the
  desktop app. `src/kkleds/browser.py` adapts the engine to the browser.
- **The keyboard** connects over **WebHID**. After the first visit, it
  reconnects without the pop-up.
- **"Light up what I play"** uses **Web MIDI**. The browser asks for permission
  the first time it's switched on.
- **Settings, saved looks and saved scenes** are stored in the browser
  (IndexedDB), so your last look comes back when you open the page again.
- **The page's `/api/...` calls** are answered inside the page by the same
  `api.dispatch()` the desktop app's local server uses.

## Limits (compared to the app)

- **Browsers:** Chrome, Edge or Opera on a computer. Firefox and Safari don't
  have WebHID.
- **Animations need the tab:** scenes run only while the tab is open. A static
  look stays on the keyboard after you close it.
- **After a reboot:** nothing happens until the page is opened again. Then it
  reconnects and restores the last look by itself.

## Build

```powershell
python webapp\build.py          # writes site\ (about 90 KB; Pyodide comes from the CDN)
```

Try it locally. `http://localhost` counts as secure, so WebHID works there:

```powershell
python -m http.server 8800 --bind 127.0.0.1 --directory site
# open http://localhost:8800 in Chrome or Edge
```

## Hosting

The site is live at
https://eliasehmann.github.io/afterglow-komplete-kontrol/.
`.github/workflows/pages.yml` rebuilds and publishes it on GitHub Pages every
time `master` changes, with that address passed as `--url`.

The built `site\` also works on any other static host with HTTPS, such as
Netlify or Cloudflare Pages.

The "Windows app" link on the page points to this repo's latest release. Set
`window.KKLEDS_APP_URL` in `webapp/kkleds-web.js` if the app is published
somewhere else.
