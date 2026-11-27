// Afterglow in the browser.
//
// Loads Python (Pyodide) with the same kkleds package the desktop app uses,
// connects the keyboard over WebHID and play-along over Web MIDI, keeps your
// settings and saved looks in the browser (IndexedDB), and answers the page's
// /api/... requests locally, so the page itself is the very same file as in the app.
(() => {
  "use strict";
  window.KKLEDS_WEB = true;
  window.KKLEDS_APP_URL = window.KKLEDS_APP_URL || "https://github.com/eliasehmann/kontrol-leds/releases/latest";

  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
  const NI_VENDOR = 0x17cc;
  const HOME = "/home/pyodide/.kkleds";  // Path.home() / ".kkleds" inside Pyodide

  let py = null, runtime = null, hid = null, midiInput = null;

  // ------------------------------------------------------------------ loading screen
  const overlay = document.createElement("div");
  overlay.style.cssText = "position:fixed;inset:0;z-index:50;display:grid;place-items:center;background:#0c0d10;" +
    "color:#eceef2;font:16px system-ui,sans-serif;text-align:center;padding:24px";
  overlay.innerHTML = `<div><div style="font-weight:700;font-size:22px;margin-bottom:10px">Afterglow</div>
    <div id="kkLoad">Loading… (the first visit downloads about 10 MB)</div></div>`;
  const showOverlay = () => document.body.appendChild(overlay);
  if (document.body) showOverlay(); else document.addEventListener("DOMContentLoaded", showOverlay);
  const say = html => { const n = overlay.querySelector("#kkLoad"); if (n) n.innerHTML = html; };

  // ------------------------------------------------------------------ WebHID
  // Only the newest report per ID is kept while one is in flight, so a busy
  // animation never builds a backlog.
  const pending = new Map();
  let pumping = false;
  async function pump() {
    if (pumping) return;
    pumping = true;
    try {
      while (pending.size && hid) {
        const [id, data] = pending.entries().next().value;
        pending.delete(id);
        try { await hid.sendReport(id, data); }
        catch (e) { console.warn("sendReport failed", e); if (!hid.opened) { hid = null; notify(); } }
      }
    } finally { pumping = false; }
  }
  const bridge = {
    hidDevice: () => (hid && hid.opened ? { vendorId: hid.vendorId, productId: hid.productId, productName: hid.productName } : null),
    sendReport: (id, data) => { pending.set(id, new Uint8Array(data)); pump(); },
    midiName: () => (midiInput ? midiInput.name : null),
  };
  function notify() { if (runtime) runtime.hid_changed(); }
  async function useDevice(d) {
    try {
      if (!d.opened) await d.open();
      hid = d; pending.clear(); notify();
    } catch (e) { console.warn("could not open", e); }
  }
  async function connectKeyboard() {
    const [d] = await navigator.hid.requestDevice({ filters: [{ vendorId: NI_VENDOR }] });
    if (d) await useDevice(d);
  }

  // ------------------------------------------------------------------ Web MIDI (play-along)
  async function enableMidi() {
    if (midiInput) return;
    const access = await navigator.requestMIDIAccess();
    const inputs = [...access.inputs.values()];
    midiInput = inputs.find(i => /mk2 midi/i.test(i.name))
      || inputs.find(i => /kontrol/i.test(i.name) && !/ext/i.test(i.name))
      || inputs[0] || null;
    if (!midiInput) throw new Error("No MIDI input found.");
    midiInput.onmidimessage = m => { if (runtime) runtime.midi_message(Array.from(m.data)); };
  }

  // ------------------------------------------------------------------ storage (saved looks, scenes, settings)
  const syncfs = populate => new Promise((ok, fail) => py.FS.syncfs(populate, err => (err ? fail(err) : ok())));

  // ------------------------------------------------------------------ start
  function loadScript(src) {
    return new Promise((ok, fail) => {
      const s = document.createElement("script");
      s.src = src; s.onload = ok; s.onerror = () => fail(new Error("Couldn't load " + src));
      document.head.appendChild(s);
    });
  }
  const ready = (async () => {
    if (!("hid" in navigator)) {
      say("This page needs <b>Chrome</b> or <b>Edge</b> on a computer to talk to your keyboard.<br><br>" +
          `Or get the <a style="color:#ff8a3d" href="${window.KKLEDS_APP_URL}">Windows app</a>.`);
      throw new Error("WebHID not supported");
    }
    await loadScript(PYODIDE + "pyodide.js");
    py = await loadPyodide({ indexURL: PYODIDE });
    py.FS.mkdirTree(HOME);
    py.FS.mount(py.FS.filesystems.IDBFS, {}, HOME);
    await syncfs(true);
    const zip = await (await fetch("kkleds.zip", { cache: "no-cache" })).arrayBuffer();
    py.unpackArchive(zip, "zip", { extractDir: "/lib/kkleds-web" });
    py.runPython("import sys; sys.path.insert(0, '/lib/kkleds-web')");
    runtime = py.pyimport("kkleds.browser").Runtime(bridge);

    // A keyboard allowed on an earlier visit reconnects without the pop-up.
    const known = (await navigator.hid.getDevices()).find(d => d.vendorId === NI_VENDOR);
    if (known) await useDevice(known);
    navigator.hid.addEventListener("connect", e => { if (!hid && e.device.vendorId === NI_VENDOR) useDevice(e.device); });
    navigator.hid.addEventListener("disconnect", e => { if (e.device === hid) { hid = null; pending.clear(); notify(); } });

    (function loop() {
      let next = 0.02;
      try { next = runtime.step(); } catch (e) { console.error(e); }
      setTimeout(loop, Math.max(4, next * 1000));
    })();
    setInterval(() => syncfs(false).catch(() => {}), 3000);
    window.addEventListener("pagehide", () => syncfs(false).catch(() => {}));
    overlay.remove();
  })();
  ready.catch(e => { console.error(e); if (e.message !== "WebHID not supported") say("Something went wrong loading Afterglow: " + e.message); });

  // ------------------------------------------------------------------ the page's /api/... calls
  const realFetch = window.fetch.bind(window);
  const reply = (code, obj) => new Response(JSON.stringify(obj), { status: code, headers: { "Content-Type": "application/json" } });
  window.fetch = async (input, opts = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (!url.pathname.startsWith("/api/")) return realFetch(input, opts);
    await ready;
    const method = (opts.method || "GET").toUpperCase(), path = url.pathname, body = opts.body || "";
    if (path === "/api/connect") {
      try { await connectKeyboard(); } catch (e) { /* closed the pop-up */ }
    }
    if (path === "/api/play" && JSON.parse(body || "{}").enabled) {
      try { await enableMidi(); }
      catch (e) { return reply(400, { error: "Allow MIDI access in the browser to light up what you play." }); }
    }
    const res = JSON.parse(runtime.api(method, path, body));
    return reply(res.code, res.body);
  };
})();
