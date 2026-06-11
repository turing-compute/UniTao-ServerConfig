import random

from extlib.flask import Blueprint, jsonify, current_app

utils_bp = Blueprint("utils", __name__)


@utils_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "success": True,
        "data": {"status": "ok"}
    })


@utils_bp.route("/mac", methods=["GET"])
def generate_mac():
    mac = [random.randint(0, 255) for _ in range(5)]
    mac_address = "0E:" + ":".join(f"{num:02X}" for num in mac)
    return jsonify({
        "success": True,
        "data": {"macAddress": mac_address}
    })
