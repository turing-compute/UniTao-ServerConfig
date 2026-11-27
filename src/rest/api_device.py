import logging
import os

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from rest.service import list_entities, read_entity_data
from kvm.device.kvm_device import KvmDevice
from kvm.vm.kvm_vm import KvmVm

device_bp = Blueprint("device", __name__)


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-Device")


def device_entries(vm_data: dict, key: str) -> list:
    """vm_data[key] when it is a list, else [] (a hand-edited JSON may hold anything)."""
    value = vm_data.get(key, None)
    return value if isinstance(value, list) else []


def managed_claims(app) -> dict:
    """Devices claimed by managed VM JSONs (usbDevices / blockDevices).

    Returns {"usb": {"<vendorId>:<productId>": [vms]}, "block": {"<kernel name>": [vms]}}.
    A stopped managed VM has no libvirt domain, so libvirt alone cannot tell that
    the device is taken: it will be grabbed again on the VM's next start. Block
    claims are keyed by the kernel name the by-id link currently resolves to; a
    device that is not plugged in claims nothing.

    A VM JSON that cannot be read is listed in claims["unreadable"]: its claims
    are unknown, not empty.
    """
    claims = {"usb": {}, "block": {}, "unreadable": []}
    for vm_name in list_entities(app, "vm"):
        try:
            vm_data = read_entity_data(app, "vm", vm_name) or {}
        except Exception:
            claims["unreadable"].append(vm_name)
            continue
        if not isinstance(vm_data, dict):
            claims["unreadable"].append(vm_name)
            continue
        for device in device_entries(vm_data, KvmVm.Keyword.UsbDevices):
            try:
                key = KvmVm.usb_device_key(device)
            except (KeyError, TypeError):
                continue
            claims["usb"].setdefault(key, []).append(vm_name)
        for device in device_entries(vm_data, KvmVm.Keyword.BlockDevices):
            path = device.get(KvmVm.Keyword.Path) if isinstance(device, dict) else None
            if not isinstance(path, str) or not os.path.exists(path):
                continue
            name = os.path.basename(os.path.realpath(path))
            claims["block"].setdefault(name, []).append(vm_name)
    return claims


def _merge(*lists) -> list:
    result = []
    for items in lists:
        for item in items:
            if item not in result:
                result.append(item)
    return result


def apply_vm_claims(devices: dict, claims: dict):
    """Add `configuredOn` to every device; a device claimed by a VM is not attachable.

    A USB disk is one device seen twice: a claim through either list (usbDevices
    or blockDevices) blocks both its USB entry and its block entry. When some VM
    JSON could not be read, nothing is attachable: its claims are unknown.
    """
    usb_devices = devices.get(KvmDevice.Keyword.Types.USB, None)
    if usb_devices is None:
        usb_devices = KvmDevice.discover_usb()
    claimed_usb = {}
    for usb in usb_devices:
        vms = _merge(claims["usb"].get(f"{usb['vendorId']}:{usb['productId']}", []),
                     *[claims["block"].get(name, []) for name in usb.get("blockDevices", [])])
        if KvmDevice.Keyword.Types.USB in devices:
            usb["configuredOn"] = vms
        if vms:
            claimed_usb[usb["id"]] = vms
            if usb.get("attachable"):
                usb["attachable"] = False
                usb["reason"] = f"configured on VM [{', '.join(vms)}]"
    for blk in devices.get(KvmDevice.Keyword.Types.Block, []):
        vms = _merge(claims["block"].get(blk["id"], []), claimed_usb.get(blk.get("usbDevice"), []))
        blk["configuredOn"] = vms
        if vms and blk["attachable"]:
            blk["attachable"] = False
            blk["reason"] = f"configured on VM [{', '.join(vms)}]"

    unreadable = claims.get("unreadable", [])
    if unreadable:
        why = f"cannot read VM JSON of [{', '.join(unreadable)}], its device claims are unknown"
        devices.setdefault("errors", {})["claims"] = why
        for kind in (KvmDevice.Keyword.Types.USB, KvmDevice.Keyword.Types.Block):
            for device in devices.get(kind, []):
                if device["attachable"]:
                    device["attachable"] = False
                    device["reason"] = why


def discover_devices(device_type: str = None) -> dict:
    """KvmDevice.discover() plus the claims of managed VM JSONs."""
    devices = KvmDevice.discover(device_type, _get_logger())
    apply_vm_claims(devices, managed_claims(current_app))
    return devices


@device_bp.route("", methods=["GET"])
def list_devices():
    """Host devices that can be attached to a VM (USB passthrough / block passthrough).

    Query:
        type        usb | block (default: both)
        attachable  true → only devices that can be attached now
    """
    device_type = request.args.get("type", None)
    if device_type is not None and device_type not in KvmDevice.Keyword.Types.list():
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": f"Invalid type [{device_type}], expect {KvmDevice.Keyword.Types.list()}"}
        }), 400

    only_attachable = request.args.get("attachable", "").lower() in ("1", "true", "yes")

    devices = discover_devices(device_type)
    if only_attachable:
        devices = {k: ([d for d in v if d["attachable"]] if isinstance(v, list) else v)
                   for k, v in devices.items()}

    return jsonify({
        "success": True,
        "data": devices
    })
