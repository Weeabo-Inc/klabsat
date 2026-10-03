# klabsat

> *"Eyes on the work."*

**A zero-dependency local dashboard** that renders what two USB device monitors write to
disk. Stdlib Python and a single self-contained HTML file. No npm. No build step. No CDN,
no external fonts, no external images. It works with the network unplugged, which is the
only honest way to ship a monitoring tool.

Born from a specific frustration: staring at a terminal wondering whether a phone was
plugged in, powered off, or sitting in a broken half-enumerated state, and having no way to
tell the difference without running another command.

---

## What it shows

Two cards, two feeds, **two independent stale indicators** — because two writers can fail
independently and a single "last updated" would hide one of them.

| Feed | File | Written by | Endpoint |
|---|---|---|---|
| Samsung phone | `state.json` | [`phonewatch`](https://github.com/Weeabo-Inc/phonewatch) | `/api/state` |
| Attached iPhone | `iphone.json` | the iPhone monitor | `/api/iphone` |

### The Samsung card

Device mode (`absent` / `download` / `mtp_or_adb` / **`descriptor_failed`**), USB
enumeration counts, COM port mapping, processes that might be holding a USB handle, LAN
reachability, and the feed's age.

### The iPhone card

Four states, clearly distinguished — because "no iPhone" and "an iPhone that won't talk to
you" are completely different problems:

| State | Meaning |
|---|---|
| `no_device` | nothing there |
| `peripheral_only` | **an Apple HID device that is not a phone** — e.g. a keyboard |
| `not_queryable` | detected over usbmux, but locked or not yet Trusted |
| `queryable` | product type, iOS version, battery, charging state |

That second state exists because an Apple Magic Keyboard and an iPhone share a vendor ID,
and an early version of this dashboard cheerfully reported a keyboard as a phone. It now
says so out loud:

```
APPLE PERIPHERAL ONLY — HID peripheral (keyboard/mouse/trackpad), NOT an iPhone
```

### Deduplication, because composite devices are liars

One physical device can enumerate as **15 separate HID collections**. Rendering them
literally produces fifteen identical rows and a dashboard nobody trusts. The server
collapses them by PID parsed from the instance ID and reports both counts:

```
device_count: 15   →   unique_device_count: 1   (1 physical device, 15 interfaces)
```

---

## Run it

```powershell
powershell -File start.ps1
```

`start.ps1` prints the exact URL, warns you if that port is already serving something, and
exits with code 3 rather than silently binding elsewhere. Or drive it directly:

```console
$ python dashboard.py [--port 8791] [--state PATH] [--history PATH] [--iphone PATH]
                      [--stale-after 10] [--iphone-stale-after 15]
```

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `8787` | loopback only — `127.0.0.1` / `localhost` / `::1` |
| `--state` | `phonewatch\state.json` | Samsung feed |
| `--history` | `phonewatch\history.jsonl` | timeline |
| `--iphone` | `phonewatch\iphone.json` | iPhone feed — point it at a sample to test with no hardware |
| `--stale-after` | `10` | seconds before the Samsung feed is flagged stale |
| `--iphone-stale-after` | `15` | same, for the iPhone feed (its writer beats every 4 s) |

---

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /` | the dashboard page |
| `GET /api/state` | Samsung snapshot + `_meta` (file ages, staleness, both writers) |
| `GET /api/iphone` | iPhone snapshot, deduplicated |
| `GET /api/history` | rolling timeline from `history.jsonl` (`?limit=N`) |
| `GET /api/health` | liveness and versions |
| anything else | `404` |

**Nothing ever 500s.** A missing, empty, truncated, or malformed JSON file is reported as
`"missing"` / `"empty"` / `"invalid json: ..."` inside a normal `200` response. A monitoring
tool that crashes when the thing it monitors misbehaves is not a monitoring tool.

---

## Testing without hardware

No phone? No problem. Two hand-written fixtures ship in the repo:

```console
$ python dashboard.py --iphone sample-iphone.json          # queryable iPhone
$ python dashboard.py --iphone sample-iphone-locked.json   # detected, not trusted
```

`_verify_render.js` runs **39 assertions** against the page's client logic under a stub DOM
— every state, the battery bar, dedup, independent staleness, and the broken-file cases.
There is no browser in CI, so the logic gets tested without one.

---

## Design notes

**Self-contained by policy.** No CDN, no external fonts or images, no telemetry. The only
occurrence of `http://` in the served HTML is inside a label.

**Polling, not websockets.** Both feeds refresh every 2 s and redraw in place. For a
dashboard that reports on two files, this is the correct amount of engineering.

**Defensive reads everywhere.** Every file read, JSON parse, and field access is guarded.
The dashboard's job is to tell you what's true, including when what's true is "the file is
garbage".

---

## Scope, honestly

**Solid:** the two cards and their states, deduplication, independent staleness, the API
surface, broken-input handling, offline operation, and the 39-assertion client test.

**Not a monitoring agent.** It reads files. Whatever writes those files is somebody else's
problem — and in this case, somebody else's repository.

**Loopback only, by design.** It binds `127.0.0.1` and rejects other hosts. It is an
instrument panel, not a service.

## License

MIT
