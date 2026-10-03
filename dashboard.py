#!/usr/bin/env python3
"""
PhoneWatch dashboard - "eyes on the work".

A zero-dependency, offline, stdlib-only HTTP server that renders whatever the
Rust `phonewatch` tool drops into state.json / history.jsonl.

Design rules (see README.md):
  * never crash on missing keys, missing files, half-written files
  * only ever bind 127.0.0.1
  * no npm, no build step, no CDN - the whole UI is index.html

Two devices are watched at once:
    * the Samsung phone  -> state.json   (endpoints /api/state, /api/history)
    * the attached iPhone -> iphone.json  (endpoint /api/iphone)

Usage:
    python dashboard.py [--port 8787] [--state PATH] [--history PATH] [--iphone PATH]
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

HERE = Path(__file__).resolve().parent

DEFAULT_STATE = Path(r"<REPO_ROOT>\phonewatch\state.json")
DEFAULT_HISTORY = Path(r"<REPO_ROOT>\phonewatch\history.jsonl")
DEFAULT_IPHONE = Path(r"<REPO_ROOT>\phonewatch\iphone.json")
DEFAULT_PORT = 8787
DEFAULT_HOST = "127.0.0.1"

STALE_SECONDS = 10.0        # front-end flags the feed as stale after this
IPHONE_STALE_SECONDS = 15.0  # the iPhone writer beats every 4s; 15s is comfortably stale
POLL_MS = 2000              # front-end poll interval
HISTORY_MAX = 600           # JSONL lines kept in memory per request
HISTORY_DEFAULT = 200

# ---- Apple USB knowledge -------------------------------------------------- #
APPLE_VID = "05ac"

# PIDs that are Apple *computer peripherals*, never a phone. Labelled as such
# so a Magic Keyboard is never mistaken for an iPhone.
APPLE_PERIPHERAL_PIDS = {
    "024f": "Apple Magic Keyboard (HID peripheral)",
    "0250": "Apple Magic Mouse / Trackpad (HID peripheral)",
    "0265": "Apple Magic Keyboard with numeric keypad (HID peripheral)",
    "0266": "Apple Magic Mouse 2 (HID peripheral)",
    "0267": "Apple Magic Trackpad 2 (HID peripheral)",
    "0291": "Apple Magic Keyboard (HID peripheral)",
    "0221": "Apple Keyboard (HID peripheral)",
    "030d": "Apple Magic Trackpad (HID peripheral)",
}

# iPhones are USB Video/PTP class devices; these PIDs are common phone/tablet modes.
APPLE_IPHONE_HINT_PIDS = {
    "12a8": "iPhone (USB 2.0, PTP/normal mode)",
    "12ac": "iPhone (USB 2.0, PTP/normal mode)",
    "12a9": "iPad",
    "12ab": "iPad",
    "1281": "iPhone (USB 2.0, recovery/DFU family)",
    "1227": "iPhone (recovery mode)",
    "1222": "iPhone (DFU mode)",
}

# process names that are known to hold a handle on the phone's USB interface
HANDLE_PATTERNS = [
    ("zadig", r"zadig"),
    ("brokkr", r"brokkr"),
    ("adb", r"(^|[\\/_\-.])adb(\.exe)?$|adb\.exe"),
    ("heimdall", r"heimdall"),
    ("odin", r"odin"),
]

VERSION = "1.1.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# defensive readers
# --------------------------------------------------------------------------- #

def read_json_defensive(path: Path):
    """Return (data, error). Never raises. Tolerates a truncated write."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, "missing"
    except PermissionError:
        # a rename right now; retry once
        time.sleep(0.03)
        try:
            raw = path.read_bytes()
        except Exception as exc:  # noqa: BLE001
            return None, f"unreadable: {type(exc).__name__}"
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable: {type(exc).__name__}: {exc}"

    if not raw.strip():
        return None, "empty"

    text = raw.decode("utf-8-sig", errors="replace")
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, f"invalid json: {exc}"


def read_history(path: Path, limit: int):
    """Return (entries, meta). Reads the tail of the JSONL file, skipping junk."""
    meta = {"path": str(path), "exists": False, "lines": 0, "bad_lines": 0,
            "returned": 0, "truncated": False, "error": None}
    try:
        if not path.is_file():
            meta["error"] = "missing"
            return [], meta
    except OSError as exc:
        meta["error"] = f"stat failed: {exc}"
        return [], meta

    meta["exists"] = True
    entries = deque(maxlen=max(1, limit))
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                meta["lines"] += 1
                if meta["lines"] > HISTORY_MAX:
                    meta["truncated"] = True
                    break
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    meta["bad_lines"] += 1
                    continue
                if isinstance(obj, dict):
                    entries.append(obj)
                else:
                    meta["bad_lines"] += 1
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"

    out = list(entries)
    meta["returned"] = len(out)
    return out, meta


def state_payload(state_path: Path, history_path: Path, stale_after: float,
                  iphone_path: Path | None = None):
    """Assemble the /api/state payload. Always returns a dict, never raises."""
    if iphone_path is None:
        iphone_path = IPHONE_PATH
    data, err = read_json_defensive(state_path)
    age = None
    mtime = None
    try:
        st = state_path.stat()
        mtime = st.st_mtime
        age = max(0.0, time.time() - st.st_mtime)
    except Exception:  # noqa: BLE001
        pass

    file_obj = {
        "path": str(state_path),
        "exists": state_path.is_file() if _safe_isfile(state_path) else False,
        "age_seconds": None if age is None else round(age, 2),
        "mtime_iso": None if mtime is None else datetime.fromtimestamp(mtime, timezone.utc)
                                                  .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "error": err,
    }

    if data is None:
        data = {}
    if not isinstance(data, dict):
        file_obj["error"] = (file_obj["error"] or "") + " (top level was not an object)"
        data = {}

    # if state.json carries no history of its own, borrow the tail of history.jsonl
    if not isinstance(data.get("history"), list):
        hist, _ = read_history(history_path, 200)
        data["history"] = hist

    stale = (age is None) or (age > stale_after)
    if not file_obj["exists"]:
        stale = True

    # the iPhone feed has its own freshness; advertise it here too so a single
    # GET /api/state is enough to tell whether EITHER writer has gone quiet.
    iphone_fresh = file_freshness(iphone_path)
    _ip_data, ip_err = read_json_defensive(iphone_path)
    iphone_fresh["error"] = ip_err
    iphone_stale_after = CONFIG.get("iphone_stale_after", IPHONE_STALE_SECONDS)
    iphone_stale = ((iphone_fresh["age_seconds"] is None)
                    or (iphone_fresh["age_seconds"] > iphone_stale_after)
                    or not iphone_fresh["exists"])

    data["_meta"] = {
        "server_time": _now_iso(),
        "server_epoch": time.time(),
        "server_pid": os.getpid(),
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "state_file": file_obj,
        "history_file": str(history_path),
        "iphone_file": iphone_fresh,
        "iphone_stale": iphone_stale,
        "iphone_stale_after_seconds": iphone_stale_after,
        "stale": stale,
        "stale_after_seconds": stale_after,
        "poll_ms": POLL_MS,
        "version": VERSION,
    }
    return data


def _safe_isfile(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:
        return False


def file_freshness(path: Path) -> dict:
    """Describe a file's presence/age without ever raising."""
    obj = {
        "path": str(path),
        "exists": _safe_isfile(path),
        "age_seconds": None,
        "mtime_iso": None,
    }
    try:
        st = path.stat()
    except Exception:  # noqa: BLE001
        return obj
    obj["age_seconds"] = round(max(0.0, time.time() - st.st_mtime), 2)
    try:
        obj["mtime_iso"] = (datetime.fromtimestamp(st.st_mtime, timezone.utc)
                            .isoformat(timespec="seconds").replace("+00:00", "Z"))
    except Exception:  # noqa: BLE001
        obj["mtime_iso"] = None
    return obj


# --------------------------------------------------------------------------- #
# iPhone (iphone.json) parsing
# --------------------------------------------------------------------------- #

_PID_RE = re.compile(r"PID_([0-9A-Fa-f]{4})")
_VID_RE = re.compile(r"VID_([0-9A-Fa-f]{4})")


def extract_ids(instance_id: str):
    """Pull (vid, pid) out of a Windows instance id. Either may be None."""
    s = instance_id or ""
    vid = _VID_RE.search(s)
    pid = _PID_RE.search(s)
    return (vid.group(1).lower() if vid else None,
            pid.group(1).lower() if pid else None)


def _as_bool(v):
    """Windows/PowerShell writers emit 'true'/'false' strings; normalise."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "yes", "1"):
            return True
        if t in ("false", "no", "0"):
            return False
    return None


def _as_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v.strip()))
        except Exception:  # noqa: BLE001
            return None
    return None


def dedupe_usb_devices(devices):
    """
    One physical Apple device shows up as MANY usb.devices rows (one per
    composite interface / HID collection). Collapse them by (vid,pid) so the
    UI shows one row per physical device instead of ~15 identical rows.

    Returns an ordered list of groups; never raises.
    """
    order = []
    groups = {}
    if not isinstance(devices, list):
        return []
    for raw in devices:
        if not isinstance(raw, dict):
            continue
        iid = raw.get("instance_id") or ""
        vid, pid = extract_ids(iid)
        # rows with no PID still deserve a slot, keyed by their friendly name
        key = (vid, pid) if pid else ("?", raw.get("friendly_name") or iid or "unknown")
        grp = groups.get(key)
        if grp is None:
            grp = {
                "vid": vid,
                "pid": pid,
                "vid_pid": (f"{vid}:{pid}" if vid and pid else (pid or "unknown")),
                "friendly_name": None,
                "interfaces": 0,
                "classes": [],
                "statuses": [],
                "instance_ids": [],
                "peripheral": False,
                "peripheral_label": None,
                "iphone_hint": False,
                "iphone_hint_label": None,
            }
            groups[key] = grp
            order.append(grp)
        grp["interfaces"] += 1
        fn = raw.get("friendly_name")
        if fn and not grp["friendly_name"]:
            grp["friendly_name"] = fn
        cls = raw.get("class")
        if cls and cls not in grp["classes"]:
            grp["classes"].append(cls)
        stt = raw.get("status")
        if stt and stt not in grp["statuses"]:
            grp["statuses"].append(stt)
        if iid and len(grp["instance_ids"]) < 12:
            grp["instance_ids"].append(iid)
        if pid:
            if pid in APPLE_PERIPHERAL_PIDS:
                grp["peripheral"] = True
                grp["peripheral_label"] = APPLE_PERIPHERAL_PIDS[pid]
            elif pid in APPLE_IPHONE_HINT_PIDS:
                grp["iphone_hint"] = True
                grp["iphone_hint_label"] = APPLE_IPHONE_HINT_PIDS[pid]
    return order


def normalize_iphone_device(dev):
    """Normalise the iphone.json `device` object (or null). Never raises."""
    if not isinstance(dev, dict):
        return None
    out = {
        "available": _as_bool(dev.get("available")),
        "udid": dev.get("udid"),
        "name": dev.get("name"),
        "product_type": dev.get("product_type"),
        "ios_version": dev.get("ios_version"),
        "build": dev.get("build"),
        "model": dev.get("model"),
        "serial": dev.get("serial"),
        "battery_pct": _as_int(dev.get("battery_pct")),
        "charging": _as_bool(dev.get("charging")),
        "charging_raw": dev.get("charging"),
        "fully_charged": _as_bool(dev.get("fully_charged")),
        "fully_charged_raw": dev.get("fully_charged"),
        "battery_keys": dev.get("battery_keys") if isinstance(dev.get("battery_keys"), list) else [],
        "error": dev.get("error"),
    }
    if out["available"] is None:
        # a device object without an explicit flag is still "something was seen"
        out["available"] = bool(out["udid"] or out["product_type"] or out["name"])
    return out


def iphone_status(data, usb, dev):
    """Classify the iPhone feed into one of four UI states. Never raises."""
    if not isinstance(data, dict) or not data:
        return "no_data"
    if dev is not None and dev.get("available") is True:
        return "queryable"
    if dev is not None:
        return "not_queryable"
    ids = data.get("usbmux_ids")
    if isinstance(ids, dict) and ids:
        return "not_queryable"
    if isinstance(ids, list) and ids:
        return "not_queryable"
    if usb.get("likely_iphone") is True:
        return "not_queryable"
    devices = usb.get("devices")
    if isinstance(devices, list) and devices:
        return "peripheral_only"
    if usb.get("present") is True:
        return "usb_activity"
    return "no_device"


def iphone_payload(iphone_path: Path, stale_after: float = IPHONE_STALE_SECONDS) -> dict:
    """
    Assemble the /api/iphone payload. Same defensive contract as
    state_payload(): always returns a dict, never raises, and reports
    "missing"/"empty"/"invalid json: ..." through _meta.error.

    The payload mirrors the on-disk field names so existing consumers of
    iphone.json keep working, and adds normalised/derived fields for the UI.
    """
    data, err = read_json_defensive(iphone_path)
    fresh = file_freshness(iphone_path)

    if data is None:
        data = {}
    elif not isinstance(data, dict):
        err = (err or "") + " (top level was not an object)"
        data = {}

    usb = data.get("usb")
    if not isinstance(usb, dict):
        usb = {}
    devices = dedupe_usb_devices(usb.get("devices"))
    raw_count = len(usb.get("devices")) if isinstance(usb.get("devices"), list) else 0

    dev = normalize_iphone_device(data.get("device"))

    ids = data.get("usbmux_ids")
    if isinstance(ids, dict):
        usbmux = list(ids.keys())
        usbmux_raw = ids
    elif isinstance(ids, list):
        usbmux = [str(x) for x in ids]
        usbmux_raw = ids
    else:
        usbmux = []
        usbmux_raw = ids

    warnings = data.get("warnings")
    if not isinstance(warnings, list):
        warnings = [] if warnings is None else [str(warnings)]
    warnings = [w for w in warnings if w not in (None, "")]

    # derived warnings the UI shows prominently even if the writer said nothing
    notes = []
    if err == "missing":
        notes.append(f"iphone.json not found at {iphone_path} - the iPhone monitor has never written a snapshot.")
    elif err:
        notes.append(f"iphone.json problem: {err}")
    peripherals = [d for d in devices if d.get("peripheral")]
    if peripherals:
        labels = ", ".join(sorted({d.get("peripheral_label") or "Apple peripheral" for d in peripherals}))
        notes.append(f"{labels} is attached. That is an Apple HID peripheral, NOT an iPhone.")
    if dev is not None and dev.get("available") is not True:
        notes.append("An Apple device is present but not queryable (typical for a locked or not-yet-trusted iPhone) - "
                     "unlock the iPhone and tap Trust.")
    if dev is None and usbmux:
        notes.append("usbmux sees a device id but no device details could be read - unlock the iPhone and tap Trust.")
    if data and usb.get("present") is False and dev is None and not usbmux:
        notes.append("No Apple USB device is attached right now.")

    status = iphone_status(data, usb, dev)

    out = {
        "seq": data.get("seq"),
        "timestamp": data.get("timestamp"),
        "usb": {
            "present": _as_bool(usb.get("present")),
            "likely_iphone": _as_bool(usb.get("likely_iphone")),
            "note": usb.get("note"),
            "device_count": raw_count,
            "unique_device_count": len(devices),
            "devices": devices,
            "peripherals": [d["vid_pid"] for d in peripherals],
            "iphone_hints": [d["vid_pid"] for d in devices if d.get("iphone_hint")],
        },
        "usbmux_ids": usbmux,
        "usbmux_ids_raw": usbmux_raw,
        "device": dev,
        "device_count": _as_int(data.get("device_count")),
        "warnings": warnings,
        "interval_ms": _as_int(data.get("interval_ms")),
        "status": status,
        "_meta": {
            "server_time": _now_iso(),
            "server_epoch": time.time(),
            "server_pid": os.getpid(),
            "uptime_seconds": round(time.time() - START_TIME, 2),
            "iphone_file": dict(fresh, error=err),
            "stale": (fresh["age_seconds"] is None) or (fresh["age_seconds"] > stale_after) or not fresh["exists"],
            "stale_after_seconds": stale_after,
            "poll_ms": POLL_MS,
            "version": VERSION,
            "derived_notes": notes,
            "raw_keys": sorted(k for k in data.keys()),
        },
    }
    out["notes"] = notes
    return out


# --------------------------------------------------------------------------- #
# process classification
# --------------------------------------------------------------------------- #

def classify_process(name: str):
    n = (name or "").strip().lower()
    for label, pat in HANDLE_PATTERNS:
        try:
            if re.search(pat, n):
                return label
        except re.error:
            continue
    return None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

START_TIME = time.time()
CONFIG = {
    "state": DEFAULT_STATE,
    "history": DEFAULT_HISTORY,
    "iphone": DEFAULT_IPHONE,
    "stale_after": STALE_SECONDS,
    "iphone_stale_after": IPHONE_STALE_SECONDS,
    "index": HERE / "index.html",
}
# convenience alias used by state_payload() so /api/state can advertise the
# iPhone feed's freshness without a second request
IPHONE_PATH = DEFAULT_IPHONE


class Handler(BaseHTTPRequestHandler):
    server_version = f"phonewatch-dashboard/{VERSION}"
    protocol_version = "HTTP/1.1"

    # ---- plumbing -------------------------------------------------------- #

    def log_message(self, fmt, *args):  # keep the console readable
        if os.environ.get("DASHBOARD_VERBOSE"):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def handle_one_request(self):
        """Same as the base implementation, minus the traceback a client that
        hangs up an idle keep-alive socket would otherwise print. The browser
        (and any fetch()-happy page) does that constantly and it is not an
        error worth a 20-line stack trace."""
        try:
            return BaseHTTPRequestHandler.handle_one_request(self)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def handle(self):
        """Run the request loop, but treat a client that vanishes (reset /
        abort / broken pipe) as a finished connection, not a server fault."""
        self.close_connection = True
        try:
            self.handle_one_request()
            while not self.close_connection:
                self.handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def _send_bytes(self, body: bytes, ctype: str, status: int = 200):
        headers = [("Content-Type", ctype)]
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        if accepts_gzip and len(body) > 1400:
            body = gzip.compress(body, 6)
            headers.append(("Content-Encoding", "gzip"))
        headers.append(("Cache-Control", "no-store, no-cache, must-revalidate"))
        try:
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send_json(self, obj, status: int = 200):
        try:
            body = json.dumps(obj, default=str, allow_nan=False).encode("utf-8")
        except Exception as exc:  # noqa: BLE001 - never let serialization kill a request
            body = json.dumps({"error": f"serialize failed: {exc}"}).encode("utf-8")
            status = 200
        self._send_bytes(body, "application/json; charset=utf-8", status)

    # ---- routing --------------------------------------------------------- #

    def do_GET(self):  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query)

            if path in ("/", "/index.html", "/dashboard", "/dashboard.html"):
                return self.serve_index()

            if path == "/api/state":
                payload = state_payload(CONFIG["state"], CONFIG["history"], CONFIG["stale_after"],
                                        CONFIG["iphone"])
                return self._send_json(payload)

            if path == "/api/iphone":
                payload = iphone_payload(CONFIG["iphone"], CONFIG["iphone_stale_after"])
                return self._send_json(payload)

            if path == "/api/history":
                try:
                    limit = int(qs.get("limit", [HISTORY_DEFAULT])[0])
                except (TypeError, ValueError):
                    limit = HISTORY_DEFAULT
                limit = max(1, min(limit, HISTORY_MAX))
                entries, meta = read_history(CONFIG["history"], limit)
                return self._send_json({"entries": entries, "meta": meta, "server_time": _now_iso()})

            if path == "/api/health":
                data, err = read_json_defensive(CONFIG["state"])
                ip_data, ip_err = read_json_defensive(CONFIG["iphone"])
                ip_fresh = file_freshness(CONFIG["iphone"])
                return self._send_json({
                    "ok": True,
                    "version": VERSION,
                    "server_time": _now_iso(),
                    "uptime_seconds": round(time.time() - START_TIME, 2),
                    "pid": os.getpid(),
                    "state_file": str(CONFIG["state"]),
                    "state_ok": data is not None,
                    "state_error": err,
                    "history_file": str(CONFIG["history"]),
                    "history_exists": _safe_isfile(CONFIG["history"]),
                    "iphone_file": str(CONFIG["iphone"]),
                    "iphone_ok": ip_data is not None,
                    "iphone_error": ip_err,
                    "iphone_age_seconds": ip_fresh["age_seconds"],
                })

            if path == "/favicon.ico":
                return self._send_bytes(b"", "image/x-icon", 204)

            return self._send_json({"error": "not found", "path": path}, 404)
        except Exception as exc:  # noqa: BLE001 - a 500 beats a dead server
            try:
                self._send_json({"error": f"handler exception: {type(exc).__name__}: {exc}"}, 500)
            except Exception:  # noqa: BLE001
                pass

    def serve_index(self):
        index = CONFIG["index"]
        try:
            body = index.read_bytes()
        except Exception as exc:  # noqa: BLE001
            html = FALLBACK_HTML.replace("__ERROR__", f"{type(exc).__name__}: {exc}")
            return self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
        self._send_bytes(body, "text/html; charset=utf-8")


FALLBACK_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>PhoneWatch dashboard - index.html missing</title>
<style>
 body{background:#0b0f14;color:#e6edf3;font:15px/1.6 ui-sans-serif,system-ui,Segoe UI,sans-serif;padding:40px}
 code{background:#161b22;padding:2px 6px;border-radius:4px;color:#79c0ff}
 .warn{color:#ffb454} h1{font-size:20px}
</style></head><body>
<h1 class="warn">dashboard markup not found</h1>
<p>The server is running, but <code>index.html</code> could not be read next to
<code>dashboard.py</code>.</p>
<p>Error: <code>__ERROR__</code></p>
<p>The JSON API is still live: <a href="/api/state"><code>/api/state</code></a> &middot;
<a href="/api/iphone"><code>/api/iphone</code></a> &middot;
<a href="/api/health"><code>/api/health</code></a></p>
</body></html>
"""


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="PhoneWatch live dashboard (stdlib only)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PHONEWATCH_DASH_PORT", DEFAULT_PORT)))
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="must stay loopback; anything else is refused")
    ap.add_argument("--state", default=os.environ.get("PHONEWATCH_STATE", str(DEFAULT_STATE)))
    ap.add_argument("--history", default=os.environ.get("PHONEWATCH_HISTORY", str(DEFAULT_HISTORY)))
    ap.add_argument("--iphone", default=os.environ.get("PHONEWATCH_IPHONE", str(DEFAULT_IPHONE)),
                    help="path to iphone.json (or a sample copy) served at /api/iphone")
    ap.add_argument("--iphone-stale-after", type=float, default=IPHONE_STALE_SECONDS)
    ap.add_argument("--stale-after", type=float, default=STALE_SECONDS)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"refusing to bind non-loopback host {args.host!r}; only 127.0.0.1 is allowed",
              file=sys.stderr)
        return 2

    CONFIG["state"] = Path(args.state).expanduser()
    CONFIG["history"] = Path(args.history).expanduser()
    CONFIG["iphone"] = Path(args.iphone).expanduser()
    CONFIG["stale_after"] = max(1.0, args.stale_after)
    CONFIG["iphone_stale_after"] = max(1.0, args.iphone_stale_after)
    global IPHONE_PATH
    IPHONE_PATH = CONFIG["iphone"]

    url = f"http://127.0.0.1:{args.port}/"
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"FATAL: cannot bind {args.host}:{args.port} - {exc}", file=sys.stderr)
        print(f"       something is already listening there. Try:  python dashboard.py --port {args.port + 1}",
              file=sys.stderr)
        return 3

    httpd.daemon_threads = True

    print("PhoneWatch dashboard")
    print(f"  URL        : {url}")
    print(f"  state.json : {CONFIG['state']}  ({'found' if _safe_isfile(CONFIG['state']) else 'NOT FOUND yet'})")
    print(f"  history    : {CONFIG['history']}  ({'found' if _safe_isfile(CONFIG['history']) else 'NOT FOUND yet'})")
    print(f"  iphone.json: {CONFIG['iphone']}  ({'found' if _safe_isfile(CONFIG['iphone']) else 'NOT FOUND yet'})")
    print(f"  stale after: {CONFIG['stale_after']:.0f}s samsung / {CONFIG['iphone_stale_after']:.0f}s iphone"
          f"   poll: {POLL_MS} ms   pid: {os.getpid()}")
    print(f"  endpoints  : /api/state  /api/history  /api/iphone  /api/health")
    print("  Ctrl+C to stop.")
    sys.stdout.flush()

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nstopping.")
    finally:
        try:
            httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
