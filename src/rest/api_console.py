#!/usr/bin/env python3

#########################################################################################
# Web VNC console for VMs.
#
# GET  /api/v1/vms/<name>/console        → noVNC console page (served by this REST API)
# POST /api/v1/vms/<name>/console        → start a one-shot websockify for the page
# GET  /api/v1/console/novnc/<path>      → noVNC static files from the host's novnc package
#
# Every console page gets its own websockify process started with --run-once: it
# proxies exactly one WebSocket connection and exits when that connection closes, so
# closing the page (or a browser crash / network drop) ends the process without the
# page having to say goodbye. --timeout ends it if the page never connects. VM stop /
# delete kills any console still open for that VM.
#
# The VM's VNC server listens on 127.0.0.1 only; websockify is the only way in from
# the network.
#########################################################################################

import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time

from extlib.flask import Blueprint, jsonify, current_app, send_from_directory, Response

from shared.logger import Log
from shared.utilities import Util

console_bp = Blueprint("console", __name__)

VNC_BASE_PORT = 5900
DEFAULT_NOVNC_DIR = "/usr/share/novnc"
DEFAULT_PORT_MIN = 6080
DEFAULT_PORT_MAX = 6099
# Seconds a freshly started websockify waits for the page to connect before exiting.
CONNECT_TIMEOUT_SEC = 30
# Seconds to wait for websockify to start listening.
STARTUP_WAIT_SEC = 5

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# vm name -> list of {"proc": Popen, "port": int}
_sessions: dict = {}
_sessions_guard = threading.Lock()


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-CONSOLE")


def _get_config() -> dict:
    return current_app.config["CONFIG"]


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _novnc_dir() -> str:
    return _get_config().get("noVncDir", DEFAULT_NOVNC_DIR)


def _port_range() -> tuple:
    config = _get_config()
    return (int(config.get("consolePortMin", DEFAULT_PORT_MIN)),
            int(config.get("consolePortMax", DEFAULT_PORT_MAX)))


def parse_vnc_display(lines: list) -> int:
    """Turn `virsh vncdisplay` output (e.g. "127.0.0.1:0" or ":1") into a TCP port."""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = re.search(r":(\d+)$", line)
        if match:
            return VNC_BASE_PORT + int(match.group(1))
    raise ValueError(f"Unrecognized vncdisplay output: {lines}")


def _vnc_port(vm_name: str):
    """Return the VM's VNC port, or None when the domain is not running."""
    try:
        result = Util.run_command(f"virsh vncdisplay {vm_name}")
    except SystemError:
        return None
    return parse_vnc_display(result.stdout_lines)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True




def _alive_sessions_locked() -> dict:
    """Drop exited processes; caller holds _sessions_guard."""
    for name in list(_sessions):
        _sessions[name] = [s for s in _sessions[name] if s["proc"].poll() is None]
        if not _sessions[name]:
            del _sessions[name]
    return _sessions


def _reap(vm_name: str, port: int, proc: subprocess.Popen):
    """Wait on the process so it never lingers as a zombie, then forget it."""
    code = proc.wait()
    _get_logger().info("Console websockify for VM [%s] on port %d exited (code %s)",
                       vm_name, port, code)
    with _sessions_guard:
        sessions = _sessions.get(vm_name, [])
        sessions[:] = [s for s in sessions if s["proc"] is not proc]
        if not sessions:
            _sessions.pop(vm_name, None)


def _start_websockify(websockify: str, vm_name: str, vnc_port: int,
                      port_min: int, port_max: int, logger):
    """Start a one-shot websockify on the first usable port; return (proc, port) or None."""
    for port in range(port_min, port_max + 1):
        with _sessions_guard:
            used = {s["port"] for ss in _alive_sessions_locked().values() for s in ss}
        if port in used or not _port_free(port):
            continue
        cmd = [websockify, "--run-once", "--timeout", str(CONNECT_TIMEOUT_SEC),
               str(port), f"127.0.0.1:{vnc_port}"]
        logger.info("Starting console for VM [%s]: %s", vm_name, " ".join(cmd))
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        deadline = time.time() + STARTUP_WAIT_SEC
        while time.time() < deadline:
            if proc.poll() is not None:
                break  # lost a race for the port, or websockify failed
            # Probe by bind, never by connecting: with --run-once a probe
            # connection could be taken as the page's one connection.
            if not _port_free(port):
                with _sessions_guard:
                    _sessions.setdefault(vm_name, []).append({"proc": proc, "port": port})
                threading.Thread(target=_reap, args=(vm_name, port, proc), daemon=True).start()
                return proc, port
            time.sleep(0.1)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
            logger.warning("websockify on port %d did not start listening in %ds",
                           port, STARTUP_WAIT_SEC)
        else:
            logger.warning("websockify on port %d exited early (code %s)", port, proc.returncode)
    return None


def close_vm_consoles(vm_name: str):
    """Kill every console process of a VM (called on VM stop / delete)."""
    with _sessions_guard:
        sessions = _sessions.pop(vm_name, [])
    for s in sessions:
        if s["proc"].poll() is None:
            s["proc"].terminate()
    if sessions:
        _get_logger().info("Closed %d console(s) of VM [%s]", len(sessions), vm_name)


@console_bp.route("/vms/<name>/console", methods=["POST"])
def open_console(name: str):
    logger = _get_logger()
    if not _NAME_RE.match(name):
        return _error("VALIDATION_ERROR", f"Invalid VM name '{name}'", 400)

    websockify = shutil.which("websockify")
    if websockify is None:
        return _error("CONSOLE_UNAVAILABLE",
                      "websockify is not installed on the host (apt install websockify)", 503)

    vnc_port = _vnc_port(name)
    if vnc_port is None:
        return _error("VM_NOT_RUNNING", f"VM '{name}' is not running or has no VNC display", 409)

    port_min, port_max = _port_range()
    started = _start_websockify(websockify, name, vnc_port, port_min, port_max, logger)
    if started is None:
        return _error("CONSOLE_UNAVAILABLE",
                      f"No free console port in {port_min}-{port_max}", 503)
    _, port = started
    return jsonify({
        "success": True,
        "data": {"name": name, "port": port, "vncPort": vnc_port,
                 "connectTimeoutSec": CONNECT_TIMEOUT_SEC}
    }), 201


@console_bp.route("/vms/<name>/console", methods=["GET"])
def console_page(name: str):
    if not _NAME_RE.match(name):
        return _error("VALIDATION_ERROR", f"Invalid VM name '{name}'", 400)
    if not os.path.isfile(os.path.join(_novnc_dir(), "core", "rfb.js")):
        return _error("CONSOLE_UNAVAILABLE",
                      f"noVNC not found in '{_novnc_dir()}' (apt install novnc, "
                      "or set noVncDir in config)", 503)
    return Response(_PAGE.replace("{{VM_NAME}}", name), mimetype="text/html")


@console_bp.route("/console/novnc/<path:filename>", methods=["GET"])
def novnc_static(filename: str):
    # send_from_directory refuses paths that escape the directory.
    response = send_from_directory(_novnc_dir(), filename)
    if filename.endswith(".js"):
        # ES modules need a JavaScript MIME type; some hosts map .js to text/plain.
        response.mimetype = "text/javascript"
    return response


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{VM_NAME}} — console</title>
<style>
  html, body { margin: 0; height: 100%; background: #1e1e1e; color: #ddd;
               font: 14px system-ui, sans-serif; }
  body { display: flex; flex-direction: column; }
  #bar { display: flex; align-items: center; gap: 12px; padding: 6px 12px;
         background: #2d2d2d; border-bottom: 1px solid #444; }
  #bar b { color: #fff; }
  #status { flex: 1; color: #aaa; }
  #status.error { color: #f88; }
  button { background: #3a3a3a; color: #eee; border: 1px solid #555;
           border-radius: 4px; padding: 4px 10px; cursor: pointer; }
  button:hover { background: #4a4a4a; }
  button:disabled { opacity: .5; cursor: default; }
  #screen { flex: 1; overflow: hidden; }
</style>
</head>
<body>
<div id="bar">
  <b>{{VM_NAME}}</b>
  <span id="status">Starting console…</span>
  <button id="cad" disabled>Ctrl+Alt+Del</button>
  <button id="reload">Reconnect</button>
</div>
<div id="screen"></div>
<script type="module">
import RFB from "/api/v1/console/novnc/core/rfb.js";

const vm = "{{VM_NAME}}";
const statusEl = document.getElementById("status");
const cadBtn = document.getElementById("cad");
document.getElementById("reload").onclick = () => location.reload();

function status(text, isError) {
  statusEl.textContent = text;
  statusEl.className = isError ? "error" : "";
}

async function start() {
  let body;
  try {
    const resp = await fetch(`/api/v1/vms/${encodeURIComponent(vm)}/console`, { method: "POST" });
    body = await resp.json();
  } catch (e) {
    status(`Failed to start console: ${e}`, true);
    return;
  }
  if (!body.success) {
    status(`Failed to start console: ${body.error.message}`, true);
    return;
  }
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.hostname}:${body.data.port}`;
  status(`Connecting to ${url}…`);
  const rfb = new RFB(document.getElementById("screen"), url);
  rfb.scaleViewport = true;
  rfb.addEventListener("connect", () => {
    status("Connected");
    cadBtn.disabled = false;
  });
  rfb.addEventListener("disconnect", (e) => {
    cadBtn.disabled = true;
    status(e.detail.clean ? "Disconnected. Click Reconnect to open a new session."
                          : "Connection lost. Click Reconnect to open a new session.", !e.detail.clean);
  });
  cadBtn.onclick = () => rfb.sendCtrlAltDel();
}

start();
</script>
</body>
</html>
"""
