#########################################################################################
# Host device passthrough for managed VMs
#
#   GET    /api/v1/vms/<name>/usb                         list the VM's USB devices
#   POST   /api/v1/vms/<name>/usb                         attach a host USB device
#   DELETE /api/v1/vms/<name>/usb/<vendorId>:<productId>  detach it
#
#   GET    /api/v1/vms/<name>/block                       list the VM's passthrough disks
#   POST   /api/v1/vms/<name>/block                       attach a host disk
#   DELETE /api/v1/vms/<name>/block/<by-id name>          detach it
#
# The VM JSON `usbDevices` / `blockDevices` lists are the source of truth: managed
# VMs are transient domains rebuilt from the JSON on every start
# (KvmVm.add_usb_hostdevs, KvmVm.create_vm_cmd).
#
# No hot-plug: attach/detach require the VM to be stopped and take effect on the
# next start. Pulling a disk out of a running guest that still has it mounted loses
# data, so the guest has to be shut down first.
#########################################################################################

import logging
import os
import re
import threading

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from shared.utilities import Util
from rest.service import read_entity_data, vm_json_path
from rest.api_vm import (
    _get_vm_lock, _get_virsh_state, _patch_error, _atomic_write_json,
)
from rest.api_device import discover_devices
from kvm.device.kvm_device import KvmDevice
from kvm.vm.kvm_vm import KvmVm

vm_device_bp = Blueprint("vm_device", __name__)

# Attach reads every VM's claims and then writes one VM's JSON; the per-VM lock
# alone lets two attaches to different VMs both see a device as free.
_claim_lock = threading.Lock()

_KEY_PATTERN = re.compile(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{4})$")
_HEX4 = re.compile(r"^(0x)?[0-9a-fA-F]{1,4}$")


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-VM-Device")


def _norm_id(value):
    """'0x0BC2' / '0bc2' / 'bc2' → '0bc2'; None when not a 16-bit hex id."""
    if not isinstance(value, str) or not _HEX4.match(value):
        return None
    if value.lower().startswith("0x"):
        value = value[2:]
    return value.lower().zfill(4)


def _usb_key(device):
    """usbDevices entry key, or None for a malformed (hand-edited) entry."""
    try:
        return KvmVm.usb_device_key(device)
    except (KeyError, TypeError):
        return None


def _block_key(device):
    """blockDevices entry key, or None for a malformed (hand-edited) entry."""
    path = device.get(KvmVm.Keyword.Path) if isinstance(device, dict) else None
    return KvmVm.block_device_key(device) if isinstance(path, str) else None


def _require_stopped(name: str, vm_data: dict):
    """(error response or None, live virsh state). Same rule as eject-iso."""
    if vm_data.get("vmState") != KvmVm.Keyword.VmStates.Stopped:
        return _patch_error(
            "VM_RUNNING",
            f"VM '{name}' must be stopped before changing passthrough devices "
            f"(vmState='{vm_data.get('vmState')}'); hot-plug is not supported", 409), None
    live_state = _get_virsh_state(name)
    if live_state == "unknown":
        return _patch_error("SYSTEM_COMMAND_FAILED", f"Cannot determine virsh state for VM '{name}'", 500), None
    if live_state == KvmVm.Keyword.VmStates.Running:
        return _patch_error("VM_RUNNING", f"VM '{name}' is still running in virsh", 409), None
    return None, live_state


def _undefine_if_persistent(vm_name: str, live_state: str, warnings: list):
    """A stopped persistent domain would be reused on start without the change;
    undefine it so the next start rebuilds the domain from the VM JSON."""
    if live_state != KvmVm.Keyword.VmStates.Stopped:
        return
    try:
        Util.run_command(f"virsh undefine {vm_name}")
    except SystemError as e:
        warnings.append(f"Failed to undefine persistent domain '{vm_name}': {e}")


@vm_device_bp.route("/<name>/usb", methods=["GET"])
def list_vm_usb(name: str):
    vm_data = read_entity_data(current_app, "vm", name)
    if vm_data is None:
        return _patch_error("NOT_FOUND", f"VM '{name}' not found", 404)

    host_usb = KvmDevice.discover_usb()
    attached = KvmDevice.attached_devices(_get_logger())["usb"]
    live_keys = {f"{a['vendorId']}:{a['productId']}" for a in attached if a["vm"] == name}

    devices = []
    for device in vm_data.get(KvmVm.Keyword.UsbDevices, None) or []:
        key = _usb_key(device)
        if key is None:
            devices.append({"entry": device, "key": None, "error": "malformed entry, fix the VM JSON"})
            continue
        host_ids = [u["id"] for u in host_usb if f"{u['vendorId']}:{u['productId']}" == key]
        entry = dict(device)
        entry.update({
            "key": key,
            "present": bool(host_ids),
            "hostDevices": host_ids,
            "liveAttached": key in live_keys,
        })
        devices.append(entry)

    return jsonify({
        "success": True,
        "data": {"name": name, "usbDevices": devices}
    })


@vm_device_bp.route("/<name>/usb", methods=["POST"])
def attach_vm_usb(name: str):
    """Attach a host USB device to a stopped VM; it is passed through from the next start.

    Body: {"device": "<id from GET /api/v1/devices?type=usb>"}
       or {"vendorId": "0bc2", "productId": "3343"}
    """
    logger = _get_logger()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _patch_error("BAD_REQUEST", "Request body must be a JSON object", 400)

    device_id = body.get("device", None)
    vendor_id = _norm_id(body.get("vendorId", None))
    product_id = _norm_id(body.get("productId", None))
    if device_id is None and (vendor_id is None or product_id is None):
        return _patch_error(
            "VALIDATION_ERROR",
            "Provide 'device' (USB id from GET /api/v1/devices?type=usb) "
            "or both 'vendorId' and 'productId' (hex)", 400)

    with _get_vm_lock(name), _claim_lock:
        vm_data = read_entity_data(current_app, "vm", name)
        if vm_data is None:
            return _patch_error("NOT_FOUND", f"VM '{name}' not found", 404)
        error, live_state = _require_stopped(name, vm_data)
        if error is not None:
            return error

        usb_devices = discover_devices(KvmDevice.Keyword.Types.USB)[KvmDevice.Keyword.Types.USB]
        if device_id is not None:
            matches = [u for u in usb_devices if u["id"] == device_id]
            if not matches:
                return _patch_error("DEVICE_NOT_FOUND", f"USB device '{device_id}' not found on host", 404)
            vendor_id, product_id = matches[0]["vendorId"], matches[0]["productId"]
        key = f"{vendor_id}:{product_id}"

        configured = vm_data.get(KvmVm.Keyword.UsbDevices, None) or []
        if any(_usb_key(d) == key for d in configured):
            return jsonify({
                "success": True,
                "data": {"name": name, "key": key, "attached": False,
                         "message": "already configured on this VM"}
            })

        matches = [u for u in usb_devices if f"{u['vendorId']}:{u['productId']}" == key]
        if not matches:
            return _patch_error("DEVICE_NOT_FOUND", f"USB device '{key}' not found on host", 404)
        if len(matches) > 1:
            # Passthrough matches by vendor:product, so identical devices are indistinguishable.
            return _patch_error(
                "AMBIGUOUS_DEVICE",
                f"{len(matches)} host USB devices share '{key}' ({', '.join(m['id'] for m in matches)}); "
                "passthrough by vendor:product cannot tell them apart", 409)
        host_device = matches[0]

        if name in host_device.get("configuredOn", []):
            # Not as USB (checked above), so through blockDevices.
            return _patch_error(
                "DEVICE_UNAVAILABLE",
                f"USB device '{host_device['id']}' ({key}) is already passed through to VM '{name}' "
                "as a block device; DELETE it from /block first", 409)

        # A stopped persistent domain of this VM may already hold it (attached by
        # hand): just record it, the domain is undefined below anyway.
        held_here = host_device.get("attachedTo") == name
        if not host_device["attachable"] and not held_here:
            return _patch_error(
                "DEVICE_UNAVAILABLE",
                f"USB device '{host_device['id']}' ({key}) cannot be attached: {host_device['reason']}", 409)

        entry = {KvmVm.Keyword.VendorId: vendor_id, KvmVm.Keyword.ProductId: product_id}
        description = " ".join(p for p in (host_device.get("manufacturer"), host_device.get("product")) if p)
        if description:
            entry["description"] = description

        vm_data[KvmVm.Keyword.UsbDevices] = configured + [entry]
        _atomic_write_json(vm_json_path(current_app, name), vm_data)

        warnings = []
        _undefine_if_persistent(name, live_state, warnings)
        logger.info("Attached USB [%s] (%s) to VM [%s]", key, host_device["id"], name)
        return jsonify({
            "success": True,
            "data": {"name": name, "key": key, "attached": True, "device": entry,
                     "hostDevice": host_device["id"], "warnings": warnings}
        }), 201


@vm_device_bp.route("/<name>/usb/<key>", methods=["DELETE"])
def detach_vm_usb(name: str, key: str):
    """Remove a USB device from a stopped VM; the next start no longer passes it through."""
    logger = _get_logger()
    m = _KEY_PATTERN.match(key)
    if not m:
        return _patch_error("VALIDATION_ERROR", f"Invalid USB key '{key}', expect <vendorId>:<productId>", 400)
    key = f"{m.group(1).lower()}:{m.group(2).lower()}"

    with _get_vm_lock(name):
        vm_data = read_entity_data(current_app, "vm", name)
        if vm_data is None:
            return _patch_error("NOT_FOUND", f"VM '{name}' not found", 404)
        error, live_state = _require_stopped(name, vm_data)
        if error is not None:
            return error

        configured = vm_data.get(KvmVm.Keyword.UsbDevices, None) or []
        remaining = [d for d in configured if _usb_key(d) != key]
        if len(remaining) == len(configured):
            return _patch_error("NOT_FOUND", f"USB device '{key}' is not configured on VM '{name}'", 404)

        if remaining:
            vm_data[KvmVm.Keyword.UsbDevices] = remaining
        else:
            vm_data.pop(KvmVm.Keyword.UsbDevices, None)
        _atomic_write_json(vm_json_path(current_app, name), vm_data)

        warnings = []
        _undefine_if_persistent(name, live_state, warnings)
        logger.info("Detached USB [%s] from VM [%s]", key, name)
        return jsonify({
            "success": True,
            "data": {"name": name, "key": key, "detached": True, "warnings": warnings}
        })


# ───────────────────────────── block device passthrough ─────────────────────────────


def _same_disk(path_a: str, path_b: str) -> bool:
    return os.path.exists(path_a) and os.path.exists(path_b) \
        and os.path.realpath(path_a) == os.path.realpath(path_b)


@vm_device_bp.route("/<name>/block", methods=["GET"])
def list_vm_block(name: str):
    vm_data = read_entity_data(current_app, "vm", name)
    if vm_data is None:
        return _patch_error("NOT_FOUND", f"VM '{name}' not found", 404)

    attached = KvmDevice.attached_devices(_get_logger())["block"]
    devices = []
    for device in vm_data.get(KvmVm.Keyword.BlockDevices, None) or []:
        key = _block_key(device)
        if key is None:
            devices.append({"entry": device, "key": None, "error": "malformed entry, fix the VM JSON"})
            continue
        path = device[KvmVm.Keyword.Path]
        present = os.path.exists(path)
        real = os.path.realpath(path) if present else None
        entry = dict(device)
        entry.update({
            "key": key,
            "present": present,
            "hostDevice": os.path.basename(real) if real else None,
            "liveAttached": real is not None and attached.get(real) == name,
        })
        devices.append(entry)

    return jsonify({
        "success": True,
        "data": {"name": name, "blockDevices": devices}
    })


@vm_device_bp.route("/<name>/block", methods=["POST"])
def attach_vm_block(name: str):
    """Pass a whole host disk through to a stopped VM, from its next start.

    Body: {"device": "<id from GET /api/v1/devices?type=block>"}  e.g. "sdb"
       or {"path": "/dev/disk/by-id/<name>"}
    The VM JSON always records a /dev/disk/by-id path: /dev/sdX is not stable.
    """
    logger = _get_logger()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _patch_error("BAD_REQUEST", "Request body must be a JSON object", 400)

    device_id = body.get("device", None)
    path = body.get("path", None)
    if device_id is None and path is None:
        return _patch_error(
            "VALIDATION_ERROR",
            "Provide 'device' (disk id from GET /api/v1/devices?type=block) "
            f"or 'path' ({KvmVm.BlockByIdDir}<name>)", 400)
    if path is not None:
        if (not isinstance(path, str) or not path.startswith(KvmVm.BlockByIdDir)
                or not KvmVm.valid_block_key(path[len(KvmVm.BlockByIdDir):])):
            return _patch_error("VALIDATION_ERROR",
                                f"Invalid path '{path}', expect {KvmVm.BlockByIdDir}<name>", 400)

    with _get_vm_lock(name), _claim_lock:
        vm_data = read_entity_data(current_app, "vm", name)
        if vm_data is None:
            return _patch_error("NOT_FOUND", f"VM '{name}' not found", 404)
        error, live_state = _require_stopped(name, vm_data)
        if error is not None:
            return error

        disks = discover_devices(KvmDevice.Keyword.Types.Block)[KvmDevice.Keyword.Types.Block]
        if device_id is not None:
            matches = [d for d in disks if d["id"] == device_id]
            if not matches:
                return _patch_error("DEVICE_NOT_FOUND", f"Disk '{device_id}' not found on host", 404)
            disk = matches[0]
            path = disk["byIdPath"]
            if path is None:
                return _patch_error(
                    "NO_STABLE_PATH",
                    f"Disk '{device_id}' has no {KvmVm.BlockByIdDir} link; "
                    "/dev/sdX names are not stable enough to record", 409)
        else:
            if not os.path.exists(path):
                return _patch_error("DEVICE_NOT_FOUND", f"'{path}' not found on host", 404)
            matches = [d for d in disks if _same_disk(d["path"], path)]
            if not matches:
                return _patch_error(
                    "VALIDATION_ERROR",
                    f"'{path}' is not a whole disk listed by GET /api/v1/devices?type=block "
                    "(partitions cannot be passed through)", 400)
            disk = matches[0]
        key = os.path.basename(path)

        configured = vm_data.get(KvmVm.Keyword.BlockDevices, None) or []
        for entry in configured:
            entry_key = _block_key(entry)
            if entry_key is None:
                continue
            if entry_key == key or _same_disk(entry[KvmVm.Keyword.Path], path):
                return jsonify({
                    "success": True,
                    "data": {"name": name, "key": entry_key, "attached": False,
                             "message": "already configured on this VM"}
                })

        if name in disk.get("configuredOn", []):
            # Not as a block device (checked above), so through usbDevices.
            return _patch_error(
                "DEVICE_UNAVAILABLE",
                f"Disk '{disk['id']}' is already passed through to VM '{name}' as USB device "
                f"{disk.get('usbDevice')}; DELETE it from /usb first", 409)

        # A stopped persistent domain of this VM may already hold it (attached by
        # hand): just record it, the domain is undefined below anyway.
        held_here = disk.get("attachedTo") == name
        if not disk["attachable"] and not held_here:
            return _patch_error(
                "DEVICE_UNAVAILABLE",
                f"Disk '{disk['id']}' ({path}) cannot be attached: {disk['reason']}", 409)

        entry = {KvmVm.Keyword.Path: path}
        if disk.get("model"):
            entry["description"] = disk["model"]
        vm_data[KvmVm.Keyword.BlockDevices] = configured + [entry]
        _atomic_write_json(vm_json_path(current_app, name), vm_data)

        warnings = []
        _undefine_if_persistent(name, live_state, warnings)
        logger.info("Attached disk [%s] (%s) to VM [%s]", path, disk["id"], name)
        return jsonify({
            "success": True,
            "data": {"name": name, "key": key, "attached": True, "device": entry,
                     "hostDevice": disk["id"], "warnings": warnings}
        }), 201


@vm_device_bp.route("/<name>/block/<key>", methods=["DELETE"])
def detach_vm_block(name: str, key: str):
    """Remove a passthrough disk from a stopped VM; the next start no longer has it."""
    logger = _get_logger()
    if not KvmVm.valid_block_key(key):
        return _patch_error("VALIDATION_ERROR", f"Invalid disk key '{key}', expect a {KvmVm.BlockByIdDir} name", 400)

    with _get_vm_lock(name):
        vm_data = read_entity_data(current_app, "vm", name)
        if vm_data is None:
            return _patch_error("NOT_FOUND", f"VM '{name}' not found", 404)
        error, live_state = _require_stopped(name, vm_data)
        if error is not None:
            return error

        configured = vm_data.get(KvmVm.Keyword.BlockDevices, None) or []
        remaining = [d for d in configured if _block_key(d) != key]
        if len(remaining) == len(configured):
            return _patch_error("NOT_FOUND", f"Disk '{key}' is not configured on VM '{name}'", 404)

        if remaining:
            vm_data[KvmVm.Keyword.BlockDevices] = remaining
        else:
            vm_data.pop(KvmVm.Keyword.BlockDevices, None)
        _atomic_write_json(vm_json_path(current_app, name), vm_data)

        warnings = []
        _undefine_if_persistent(name, live_state, warnings)
        logger.info("Detached disk [%s] from VM [%s]", key, name)
        return jsonify({
            "success": True,
            "data": {"name": name, "key": key, "detached": True, "warnings": warnings}
        })
