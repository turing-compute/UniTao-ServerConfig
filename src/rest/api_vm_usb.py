#########################################################################################
# USB passthrough for managed VMs
#
#   GET    /api/v1/vms/<name>/usb                         list the VM's USB devices
#   POST   /api/v1/vms/<name>/usb                         attach a host USB device
#   DELETE /api/v1/vms/<name>/usb/<vendorId>:<productId>  detach it
#
# The VM JSON `usbDevices` list is the source of truth: managed VMs are transient
# domains rebuilt from the JSON on every start (KvmVm.add_usb_hostdevs). A running
# VM is additionally hot-plugged / unplugged with `virsh attach|detach-device --live`.
#########################################################################################

import logging
import os
import re
import tempfile

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


def _virsh_device(action: str, vm_name: str, device: dict):
    """Hot-plug (attach) or unplug (detach) one USB device on a running VM."""
    xml = KvmVm.usb_hostdev_xml(device, optional=False)
    fd, path = tempfile.mkstemp(prefix=f"usb-{vm_name}-", suffix=".xml")
    try:
        with os.fdopen(fd, "w") as fp:
            fp.write(xml)
        Util.run_command(f"virsh {action}-device {vm_name} {path} --live")
    finally:
        os.remove(path)


def _live_attached(vm_name: str, key: str) -> bool:
    """Whether the VM's live domain has a USB hostdev for <vendorId>:<productId>."""
    for item in KvmDevice.attached_devices(_get_logger())["usb"]:
        if item["vm"] == vm_name and f"{item['vendorId']}:{item['productId']}" == key:
            return True
    return False


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
    """Attach a host USB device to a VM.

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

        # Already attached to this very VM by hand: just record it.
        live_here = host_device.get("attachedTo") == name
        if not host_device["attachable"] and not live_here:
            return _patch_error(
                "DEVICE_UNAVAILABLE",
                f"USB device '{host_device['id']}' ({key}) cannot be attached: {host_device['reason']}", 409)

        entry = {KvmVm.Keyword.VendorId: vendor_id, KvmVm.Keyword.ProductId: product_id}
        description = " ".join(p for p in (host_device.get("manufacturer"), host_device.get("product")) if p)
        if description:
            entry["description"] = description

        live_state = _get_virsh_state(name)
        hotplugged = False
        if live_state == KvmVm.Keyword.VmStates.Running and not live_here:
            logger.info("Hot-plug USB [%s] into running VM [%s]", key, name)
            _virsh_device("attach", name, entry)
            hotplugged = True

        vm_data[KvmVm.Keyword.UsbDevices] = configured + [entry]
        try:
            _atomic_write_json(vm_json_path(current_app, name), vm_data)
        except Exception:
            if hotplugged:
                try:
                    _virsh_device("detach", name, entry)
                except SystemError as e:
                    logger.error("Rollback detach of USB [%s] from VM [%s] failed: %s", key, name, e)
            raise

        warnings = []
        _undefine_if_persistent(name, live_state, warnings)
        logger.info("Attached USB [%s] (%s) to VM [%s], hotplugged=%s", key, host_device["id"], name, hotplugged)
        return jsonify({
            "success": True,
            "data": {"name": name, "key": key, "attached": True, "device": entry,
                     "hostDevice": host_device["id"], "hotplugged": hotplugged,
                     "warnings": warnings}
        }), 201


@vm_usb_bp.route("/<name>/usb/<key>", methods=["DELETE"])
def detach_vm_usb(name: str, key: str):
    logger = _get_logger()
    m = _KEY_PATTERN.match(key)
    if not m:
        return _patch_error("VALIDATION_ERROR", f"Invalid USB key '{key}', expect <vendorId>:<productId>", 400)
    key = f"{m.group(1).lower()}:{m.group(2).lower()}"

    with _get_vm_lock(name):
        vm_data = read_entity_data(current_app, "vm", name)
        if vm_data is None:
            return _patch_error("NOT_FOUND", f"VM '{name}' not found", 404)

        configured = vm_data.get(KvmVm.Keyword.UsbDevices, None) or []
        remaining = [d for d in configured if KvmVm.usb_device_key(d) != key]
        if len(remaining) == len(configured):
            return _patch_error("NOT_FOUND", f"USB device '{key}' is not configured on VM '{name}'", 404)
        entry = next(d for d in configured if KvmVm.usb_device_key(d) == key)

        live_state = _get_virsh_state(name)
        unplugged = False
        if live_state == KvmVm.Keyword.VmStates.Running and _live_attached(name, key):
            logger.info("Unplug USB [%s] from running VM [%s]", key, name)
            _virsh_device("detach", name, entry)
            unplugged = True

        if remaining:
            vm_data[KvmVm.Keyword.UsbDevices] = remaining
        else:
            vm_data.pop(KvmVm.Keyword.UsbDevices, None)
        _atomic_write_json(vm_json_path(current_app, name), vm_data)

        warnings = []
        _undefine_if_persistent(name, live_state, warnings)
        logger.info("Detached USB [%s] from VM [%s], unplugged=%s", key, name, unplugged)
        return jsonify({
            "success": True,
            "data": {"name": name, "key": key, "detached": True, "unplugged": unplugged,
                     "warnings": warnings}
        })
