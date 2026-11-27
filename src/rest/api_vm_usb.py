#########################################################################################
# USB passthrough for managed VMs
#
#   GET    /api/v1/vms/<name>/usb                         list the VM's USB devices
#   POST   /api/v1/vms/<name>/usb                         attach a host USB device
#   DELETE /api/v1/vms/<name>/usb/<vendorId>:<productId>  detach it
#
# The VM JSON `usbDevices` list is the source of truth: managed VMs are transient
# domains rebuilt from the JSON on every start (KvmVm.add_usb_hostdevs).
#
# No hot-plug: attach/detach require the VM to be stopped and take effect on the
# next start. Pulling a disk out of a running guest that still has it mounted loses
# data, so the guest has to be shut down first.
#########################################################################################

import logging
import re

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

vm_usb_bp = Blueprint("vm_usb", __name__)

_KEY_PATTERN = re.compile(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{4})$")
_HEX4 = re.compile(r"^(0x)?[0-9a-fA-F]{1,4}$")


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-VM-USB")


def _norm_id(value):
    """'0x0BC2' / '0bc2' / 'bc2' → '0bc2'; None when not a 16-bit hex id."""
    if not isinstance(value, str) or not _HEX4.match(value):
        return None
    if value.lower().startswith("0x"):
        value = value[2:]
    return value.lower().zfill(4)


def _require_stopped(name: str, vm_data: dict):
    """(error response or None, live virsh state). Same rule as eject-iso."""
    if vm_data.get("vmState") != KvmVm.Keyword.VmStates.Stopped:
        return _patch_error(
            "VM_RUNNING",
            f"VM '{name}' must be stopped before changing USB devices "
            f"(vmState='{vm_data.get('vmState')}'); hot-plug is not supported", 409), None
    live_state = _get_virsh_state(name)
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


@vm_usb_bp.route("/<name>/usb", methods=["GET"])
def list_vm_usb(name: str):
    vm_data = read_entity_data(current_app, "vm", name)
    if vm_data is None:
        return _patch_error("NOT_FOUND", f"VM '{name}' not found", 404)

    host_usb = KvmDevice.discover_usb()
    attached = KvmDevice.attached_devices(_get_logger())["usb"]
    live_keys = {f"{a['vendorId']}:{a['productId']}" for a in attached if a["vm"] == name}

    devices = []
    for device in vm_data.get(KvmVm.Keyword.UsbDevices, None) or []:
        key = KvmVm.usb_device_key(device)
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


@vm_usb_bp.route("/<name>/usb", methods=["POST"])
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

    with _get_vm_lock(name):
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
        if any(KvmVm.usb_device_key(d) == key for d in configured):
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


@vm_usb_bp.route("/<name>/usb/<key>", methods=["DELETE"])
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
        remaining = [d for d in configured if KvmVm.usb_device_key(d) != key]
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
