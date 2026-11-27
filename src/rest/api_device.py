import logging

from extlib.flask import Blueprint, request, jsonify

from shared.logger import Log
from kvm.device.kvm_device import KvmDevice

device_bp = Blueprint("device", __name__)


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-Device")


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

    devices = KvmDevice.discover(device_type, _get_logger())
    if only_attachable:
        devices = {k: [d for d in v if d["attachable"]] for k, v in devices.items()}

    return jsonify({
        "success": True,
        "data": devices
    })
