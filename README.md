<div align="center">
	<img width=140 src="assets/cover.svg" />
	<h2>klabsat</h2>
</div>

[![License](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)]()
[![Platform](https://img.shields.io/badge/platform-anywhere%20with%20Python-3776AB.svg?style=flat-square)]()
[![Language](https://img.shields.io/badge/language-Python-3776AB.svg?style=flat-square)]()
[![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg?style=flat-square)]()

### Eyes on the work, and nothing else.

A zero-dependency local dashboard that renders what two USB device monitors write to disk. Stdlib Python and one self-contained HTML file. No npm, no build step, no CDN, no external fonts, no external images. It works with the network unplugged, which is the only honest way to ship a monitoring tool.

Born from a specific frustration: staring at a terminal wondering whether a phone was plugged in, powered off, or sitting in a broken half-enumerated state, with no way to tell the difference short of running another command.

---

### What does this do?

Two cards, two feeds, **two independent stale indicators**, because two writers can fail independently and a single "last updated" would hide one of them.

| Feed | File | Written by | Endpoint |
|---|---|---|---|
| Samsung phone | `state.json` | [phonewatch](https://github.com/Weeabo-Inc/phonewatch) | `/api/state` |
| Attached iPhone | `iphone.json` | [iwhale](https://github.com/Weeabo-Inc/iwhale) | `/api/iphone` |

#### The Samsung card

Device mode (`absent`, `download`, `mtp_or_adb`, `descriptor_failed`), USB enumeration counts, COM port mapping, processes that might be holding a USB handle, LAN reachability, and the feed's age.

#### The iPhone card

Four states, clearly distinguished, because "no iPhone" and "an iPhone that will not talk to you" are completely different problems:

| State | Meaning |
|---|---|
| `no_device` | nothing there |
| `peripheral_only` | **an Apple HID device that is not a phone**, such as a keyboard |
| `not_queryable` | detected over usbmux, but locked or not yet Trusted |
| `queryable` | product type, iOS version, battery, charging state |

That second state exists because an Apple Magic Keyboard and an iPhone share a vendor ID, and an early version of this dashboard cheerfully reported a keyboard as a phone. It now says so out loud:

```
APPLE PERIPHERAL ONLY - HID peripheral (keyboard/mouse/trackpad), NOT an iPhone
```

---

### Deduplication, because composite devices are liars

One physical device can enumerate as **15 separate HID collections**. Rendering them literally produces fifteen identical rows and a dashboard nobody trusts. The server collapses them by PID parsed from the instance ID and reports both counts:

```
device_count: 15   ->   unique_device_count: 1   (1 physical device, 15 interfaces)
```

---

### How do I use it?

```powershell
powershell -File start.ps1
```

`start.ps1` prints the exact URL, warns you if that port is already serving something, and exits with code 3 rather than silently binding somewhere else. Or drive the server directly:

```console
$ python dashboard.py [--port 8791] [--state PATH] [--history PATH] [--iphone PATH]
                      [--stale-after 10] [--iphone-stale-after 15]
```

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `8787` | loopback only: `127.0.0.1`, `localhost`, `::1` |
| `--state` | `state.json` | Samsung feed |
| `--history` | `history.jsonl` | timeline |
| `--iphone` | `iphone.json` | iPhone feed. Point it at a sample to test with no hardware. |
| `--stale-after` | `10` | seconds before the Samsung feed is flagged stale |
| `--iphone-stale-after` | `15` | same, for the iPhone feed, whose writer beats every 4 s |

---

### Endpoints

| Endpoint | Returns |
|---|---|
| `GET /` | the dashboard page |
| `GET /api/state` | Samsung snapshot plus `_meta` (file ages, staleness, both writers) |
| `GET /api/iphone` | iPhone snapshot, deduplicated |
| `GET /api/history` | rolling timeline from `history.jsonl` (`?limit=N`) |
| `GET /api/health` | liveness and versions |
| anything else | `404` |

**Nothing ever 500s.** A missing, empty, truncated, or malformed JSON file is reported as `"missing"`, `"empty"`, or `"invalid json: ..."` inside a normal `200` response. A monitoring tool that crashes when the thing it monitors misbehaves is not a monitoring tool.

---

### Testing without hardware

No phone? No problem. Two hand-written fixtures ship in the repo:

```console
$ python dashboard.py --iphone sample-iphone.json          # queryable iPhone
$ python dashboard.py --iphone sample-iphone-locked.json   # detected, not trusted
```

`_verify_render.js` runs **39 assertions** against the page's client logic under a stub DOM: every state, the battery bar, dedup, independent staleness, and the broken-file cases. There is no browser in CI, so the logic gets tested without one.

---

### How it works under the hood

**Self-contained by policy.** No CDN, no external fonts or images, no telemetry. The only occurrence of `http://` in the served HTML is inside a label.

**Polling, not websockets.** Both feeds refresh every 2 seconds and redraw in place. For a dashboard reporting on two files, this is the correct amount of engineering.

**Defensive reads everywhere.** Every file read, JSON parse, and field access is guarded. The dashboard's job is to tell you what is true, including when what is true is that the file is garbage.

---

### What it can and can't do

**Can do:**
- Render two independent device feeds with separate staleness tracking
- Deduplicate composite USB devices into physical devices
- Survive missing, empty, and malformed input without a 500
- Run fully offline with no package installation
- Serve a tested client with 39 assertions behind it

**Can't do:**
- Monitor anything. It reads files. Whatever writes those files is somebody else's repository.
- Listen on a non-loopback address. It is an instrument panel, not a service.
- Persist anything. Restart it and it is a blank page again.

---

### Project layout

```
klabsat/
├── assets/
│   └── cover.svg
├── dashboard.py              stdlib HTTP server
├── index.html                single self-contained page
├── start.ps1                 launcher, prints the URL
├── sample-iphone.json        fixture: queryable iPhone
├── sample-iphone-locked.json fixture: detected, not trusted
└── _verify_render.js         39 client-side assertions
```

---

### Credits

- [phonewatch](https://github.com/Weeabo-Inc/phonewatch) and [iwhale](https://github.com/Weeabo-Inc/iwhale) for the data
- [Twemoji](https://github.com/twitter/twemoji) for the cover art, CC BY 4.0
- Python's `http.server`, which is doing more work here than it ever expected to

---

<div align="center">
	<br/>
	<i>no npm. no build step. no network. on purpose.</i>
	<br/>
	<sub>if it cannot run on an airgapped laptop, it is not a monitoring tool.</sub>
</div>
