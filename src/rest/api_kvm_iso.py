"""REST API for ISO installation media (/api/v1/kvm_iso).

An ISO is read-only install media that a VM can mount as a cdrom. It is kept
apart from images so it can never be used as a disk or a backing file.

    {isoDataDir}/<name>.json   — definition (isoPath, isoSource, downloadState, ...)
    {isoFileDir}/<name>.iso    — the media file

Sources:
    remote — POST /iso/<name> with downloadLink; downloaded in the background.
    upload — PUT  /iso/<name> with the raw file as the request body (streamed).
"""

import hashlib
import json
import logging
import os
import re
import threading
import time

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from rest.service import (
    list_entities, read_entity_data, write_entity_data, get_entity_path,
    get_data_dir, get_iso_file_dir, delete_entity,
)
from kvm.iso.kvm_iso import KvmIso

kvm_iso_bp = Blueprint("kvm_iso", __name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UPLOAD_CHUNK = 4 * 1024 * 1024
_IN_PROGRESS = "in-progress"
_READY = "ready"
_FAILED = "failed"


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-ISO")


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _strip_ext(name: str) -> str:
    return name[:-len(KvmIso.Extension)] if name.endswith(KvmIso.Extension) else name


def _validate_name(name: str):
    """ISO names become file names; reject anything that could escape the directories."""
    if not _NAME_RE.match(name) or name.endswith(KvmIso.Extension):
        return _error("VALIDATION_ERROR",
                      f"Invalid ISO name '{name}': use letters, digits, '.', '_' or '-', "
                      f"not starting with '.'/'-' and without the '{KvmIso.Extension}' suffix", 400)
    return None


def _abs_iso_path(app, data: dict) -> str:
    iso_path = data.get(KvmIso.Keyword.IsoPath, "")
    if os.path.isabs(iso_path):
        return iso_path
    return os.path.normpath(os.path.join(get_data_dir(app, "kvm_iso"), iso_path))


def _default_iso_path(app, name: str) -> str:
    """isoPath stored relative to isoDataDir, same convention as imagePath."""
    abs_path = os.path.join(get_iso_file_dir(app), f"{name}{KvmIso.Extension}")
    try:
        return os.path.relpath(abs_path, get_data_dir(app, "kvm_iso"))
    except ValueError:
        return abs_path


def _is_busy(data: dict) -> bool:
    state = data.get("downloadState")
    return state == _IN_PROGRESS or isinstance(state, dict)


def _persist_state(data_file_path: str, download_state, extra: dict = None):
    try:
        with open(data_file_path, "r") as f:
            persisted = json.load(f)
    except Exception:
        persisted = {}
    persisted["downloadState"] = download_state
    if extra:
        persisted.update(extra)
    with open(data_file_path, "w") as f:
        json.dump(persisted, f, indent=4)


def _remove_iso_files(app, data: dict, name: str, logger: logging.Logger):
    if not data.get(KvmIso.Keyword.IsoPath):
        return
    abs_path = _abs_iso_path(app, data)
    for path in (abs_path, abs_path + ".tmp"):
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Removed ISO file [{path}] for [{name}]")


def recover_stale_kvm_iso(app):
    """On startup, mark 'in-progress' downloads/uploads as failed and drop temp files.

    A service restart means the worker threads are gone.
    """
    logger = _get_logger()
    data_dir = get_data_dir(app, "kvm_iso")
    if not os.path.isdir(data_dir):
        return
    for name in list_entities(app, "kvm_iso"):
        data_file = get_entity_path(app, "kvm_iso", name)
        try:
            with open(data_file, "r") as f:
                data = json.load(f)
        except Exception:
            continue
        if not _is_busy(data):
            continue
        if data.get(KvmIso.Keyword.IsoPath):
            tmp_path = _abs_iso_path(app, data) + ".tmp"
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                logger.info(f"Removed stale temp file [{tmp_path}] for ISO [{name}]")
        data["downloadState"] = _FAILED
        with open(data_file, "w") as f:
            json.dump(data, f, indent=4)
        logger.info(f"Recovered stale ISO [{name}]: in-progress → failed")


def _run_iso_download(data_file_path: str, name: str):
    """Background thread: download the ISO, then persist the final downloadState."""
    logger = _get_logger()
    last_update = [0.0]

    def progress_callback(tmp_path, current_size, total_size):
        now = time.time()
        if now - last_update[0] < 1.0:
            return
        last_update[0] = now
        percent = (current_size * 100) // total_size if total_size > 0 else None
        _persist_state(data_file_path, {
            "phase": "downloading",
            "tempFilePath": tmp_path,
            "tempFileSize": current_size,
            "totalSize": total_size,
            "progressPercent": percent,
        })

    try:
        KvmIso(data_file_path, logger, progress_callback=progress_callback).Create()
        state = _READY
    except Exception as e:
        logger.error(f"ISO [{name}] download failed: {e}")
        state = _FAILED
    _persist_state(data_file_path, state)


def _check_replaceable(prev: dict, name: str):
    """An existing ISO may only be replaced when its last attempt failed."""
    if prev is None:
        return None
    if _is_busy(prev):
        return _error("CONFLICT", f"ISO '{name}' is still being transferred", 409)
    if prev.get("downloadState") == _READY:
        return _error("CONFLICT", f"ISO '{name}' already exists; DELETE it first to replace it", 409)
    return None


# ── Routes ──

@kvm_iso_bp.route("", methods=["GET"])
def list_kvm_iso():
    managed = set(list_entities(current_app, "kvm_iso"))
    iso_dir = get_iso_file_dir(current_app)
    disk_files = {}
    if os.path.isdir(iso_dir):
        for f in sorted(os.listdir(iso_dir)):
            if f.endswith(KvmIso.Extension) and os.path.isfile(os.path.join(iso_dir, f)):
                disk_files[_strip_ext(f)] = f

    items = []
    for name in sorted(managed):
        data = read_entity_data(current_app, "kvm_iso", name) or {}
        items.append({
            "name": name,
            "managed": True,
            "file": disk_files.pop(name, None),
            "isoSource": data.get(KvmIso.Keyword.IsoSource),
            "downloadState": data.get("downloadState"),
        })
    for name, f in sorted(disk_files.items()):
        items.append({"name": name, "managed": False, "file": f})

    return jsonify({"success": True, "data": {"kvm_iso": items}})


@kvm_iso_bp.route("/<name>", methods=["GET"])
def get_iso(name: str):
    name = _strip_ext(name)
    data = read_entity_data(current_app, "kvm_iso", name)
    if data is not None:
        data["id"] = name
        data["managed"] = True
        abs_path = _abs_iso_path(current_app, data)
        data["sizeInBytes"] = os.path.getsize(abs_path) if os.path.isfile(abs_path) else None
        return jsonify({"success": True, "data": data})

    abs_path = os.path.join(get_iso_file_dir(current_app), f"{name}{KvmIso.Extension}")
    if os.path.isfile(abs_path):
        return jsonify({"success": True, "data": {
            "id": name,
            "managed": False,
            "file": os.path.basename(abs_path),
            "isoPath": _default_iso_path(current_app, name),
            "sizeInBytes": os.path.getsize(abs_path),
        }})

    return _error("NOT_FOUND", f"ISO '{name}' not found", 404)


@kvm_iso_bp.route("/<name>", methods=["POST"])
def download_iso(name: str):
    """Download an ISO from a remote URL (async).

    Body: {"downloadLink": "https://...", "sha256": "<optional hex digest>"}
    Poll GET /api/v1/kvm_iso/<name> until downloadState is "ready" or "failed".
    """
    logger = _get_logger()
    err = _validate_name(name)
    if err:
        return err
    if not request.is_json:
        return _error("BAD_REQUEST", "Content-Type must be application/json", 400)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("BAD_REQUEST", "Request body is not a valid JSON object", 400)

    allowed = {KvmIso.Keyword.DownloadLink, KvmIso.Keyword.Sha256}
    unknown = sorted(set(body) - allowed)
    if unknown:
        return _error("VALIDATION_ERROR", f"Unknown fields {unknown}, allowed: {sorted(allowed)}", 400)
    link = body.get(KvmIso.Keyword.DownloadLink)
    if not isinstance(link, str) or not link.startswith(("http://", "https://")):
        return _error("VALIDATION_ERROR", "'downloadLink' must be an http(s) URL", 400)
    sha256 = body.get(KvmIso.Keyword.Sha256)
    if sha256 is not None and not KvmIso.is_sha256(sha256):
        return _error("VALIDATION_ERROR", "'sha256' must be 64 hex characters", 400)

    prev = read_entity_data(current_app, "kvm_iso", name)
    err = _check_replaceable(prev, name)
    if err:
        return err
    if prev is not None:
        _remove_iso_files(current_app, prev, name, logger)

    data = {
        KvmIso.Keyword.IsoPath: _default_iso_path(current_app, name),
        KvmIso.Keyword.IsoSource: KvmIso.Keyword.Source.Remote,
        KvmIso.Keyword.DownloadLink: link,
        "downloadState": _IN_PROGRESS,
    }
    if sha256 is not None:
        data[KvmIso.Keyword.Sha256] = sha256.lower()
    write_entity_data(current_app, "kvm_iso", name, data)
    os.makedirs(get_iso_file_dir(current_app), exist_ok=True)
    logger.info(f"Download ISO [{name}] from [{link}] — spawning background thread")

    threading.Thread(
        target=_run_iso_download,
        args=(get_entity_path(current_app, "kvm_iso", name), name),
        daemon=True,
    ).start()

    return jsonify({"success": True, "data": {
        "name": name, "isoPath": data[KvmIso.Keyword.IsoPath], "downloadState": _IN_PROGRESS,
    }}), 200 if prev is not None else 202


@kvm_iso_bp.route("/<name>", methods=["PUT"])
def upload_iso(name: str):
    """Upload an ISO as the raw request body (streamed to disk, never held in memory).

    curl -T ubuntu.iso -H 'Content-Type: application/octet-stream' \\
         'http://host:5000/api/v1/kvm_iso/<name>?sha256=<optional hex digest>'
    """
    logger = _get_logger()
    err = _validate_name(name)
    if err:
        return err
    if request.mimetype != "application/octet-stream":
        return _error("BAD_REQUEST", "Content-Type must be application/octet-stream", 400)
    if request.content_length is None:
        return _error("BAD_REQUEST", "Content-Length header is required", 411)
    sha256 = request.args.get(KvmIso.Keyword.Sha256)
    if sha256 is not None and not KvmIso.is_sha256(sha256):
        return _error("VALIDATION_ERROR", "'sha256' must be 64 hex characters", 400)

    prev = read_entity_data(current_app, "kvm_iso", name)
    err = _check_replaceable(prev, name)
    if err:
        return err
    if prev is not None:
        _remove_iso_files(current_app, prev, name, logger)

    data = {
        KvmIso.Keyword.IsoPath: _default_iso_path(current_app, name),
        KvmIso.Keyword.IsoSource: KvmIso.Keyword.Source.Upload,
        "downloadState": _IN_PROGRESS,
    }
    if sha256 is not None:
        data[KvmIso.Keyword.Sha256] = sha256.lower()
    write_entity_data(current_app, "kvm_iso", name, data)
    data_file = get_entity_path(current_app, "kvm_iso", name)

    abs_path = _abs_iso_path(current_app, data)
    tmp_path = abs_path + ".tmp"
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    expected_size = request.content_length
    logger.info(f"Upload ISO [{name}] ({expected_size} bytes) to [{tmp_path}]")

    digest = hashlib.sha256()
    received = 0
    try:
        with open(tmp_path, "wb") as f:
            while True:
                chunk = request.stream.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                received += len(chunk)
        if received != expected_size:
            raise ValueError(f"incomplete upload: received {received} of {expected_size} bytes")
        actual = digest.hexdigest()
        if sha256 is not None and actual != sha256.lower():
            raise ValueError(f"sha256 mismatch: expect [{sha256.lower()}], got [{actual}]")
        os.rename(tmp_path, abs_path)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        _persist_state(data_file, _FAILED)
        logger.error(f"Upload ISO [{name}] failed: {e}")
        return _error("VALIDATION_ERROR", f"Upload failed: {e}", 400)

    # Always record the digest of what was stored so later checks can verify it.
    _persist_state(data_file, _READY, {KvmIso.Keyword.Sha256: actual})
    logger.info(f"Upload ISO [{name}] complete, sha256={actual}")
    return jsonify({"success": True, "data": {
        "name": name, "isoPath": data[KvmIso.Keyword.IsoPath], "downloadState": _READY,
        "sizeInBytes": received, "sha256": actual,
    }}), 200 if prev is not None else 201


@kvm_iso_bp.route("/<name>", methods=["DELETE"])
def delete_iso(name: str):
    logger = _get_logger()
    name = _strip_ext(name)
    data = read_entity_data(current_app, "kvm_iso", name)
    if data is not None:
        if _is_busy(data):
            return _error("CONFLICT", f"ISO '{name}' is still being transferred", 409)
        _remove_iso_files(current_app, data, name, logger)
        delete_entity(current_app, "kvm_iso", name)
        logger.info(f"Delete managed ISO [{name}]")
        return jsonify({"success": True, "data": {"name": name, "deleted": True}})

    abs_path = os.path.join(get_iso_file_dir(current_app), f"{name}{KvmIso.Extension}")
    if _NAME_RE.match(name) and os.path.isfile(abs_path):
        os.remove(abs_path)
        logger.info(f"Delete unmanaged ISO [{name}] file [{abs_path}]")
        return jsonify({"success": True, "data": {"name": name, "deleted": True}})

    return _error("NOT_FOUND", f"ISO '{name}' not found", 404)
