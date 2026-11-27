import logging

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from rest.service import list_entities, read_entity_data
from kvm.device.kvm_device import KvmDevice
from kvm.vm.kvm_vm import KvmVm

device_bp = Blueprint("device", __name__)


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-Device")


def managed_usb_claims(app) -> dict:
    """{"<vendorId>:<productId>": [vm names]} from the usbDevices of managed VM JSONs.

    A stopped managed VM has no libvirt domain, so libvirt alone cannot tell that
    the device is taken: it will be grabbed again on the VM's next start.
    """
    claims = {}
    for vm_name in list_entities(app, "vm"):
        try:
            vm_data = read_entity_data(app, "vm", vm_name) or {}
        except Exception:
            continue
        for device in vm_data.get(KvmVm.Keyword.UsbDevices, None) or []:
            try:
                key = KvmVm.usb_device_key(device)
            except (KeyError, TypeError):
                continue
            claims.setdefault(key, []).append(vm_name)
    return claims


def apply_vm_claims(devices: dict, claims: dict):
    """Add `configuredOn` to USB devices; a device claimed by a VM is not attachable.

    The claim also blocks the USB disk's entry in the block list.
    """
    usb_devices = devices.get(KvmDevice.Keyword.Types.USB, None)
    if usb_devices is None:
        usb_devices = KvmDevice.discover_usb()
    claimed_usb = {}
    for usb in usb_devices:
        vms = claims.get(f"{usb['vendorId']}:{usb['productId']}", [])
        if KvmDevice.Keyword.Types.USB in devices:
            usb["configuredOn"] = vms
        if vms:
            claimed_usb[usb["id"]] = vms
            if usb.get("attachable"):
                usb["attachable"] = False
                usb["reason"] = f"configured on VM [{', '.join(vms)}]"
    for blk in devices.get(KvmDevice.Keyword.Types.Block, []):
        vms = claimed_usb.get(blk.get("usbDevice"))
        if vms and blk["attachable"]:
            blk["attachable"] = False
            blk["reason"] = f"configured on VM [{', '.join(vms)}] as USB device {blk['usbDevice']}"


def discover_devices(device_type: str = None) -> dict:
    """KvmDevice.discover() plus the claims of managed VM JSONs."""
    devices = KvmDevice.discover(device_type, _get_logger())
    apply_vm_claims(devices, managed_usb_claims(current_app))
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
        devices = {k: [d for d in v if d["attachable"]] for k, v in devices.items()}

    return jsonify({
        "success": True,
        "data": devices
    })
