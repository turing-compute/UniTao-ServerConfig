import logging

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from rest.service import list_entities, read_entity_data, write_entity_data, delete_entity, get_entity_path, get_data_dir
from network.bridge.net_bridge import NetBridge

bridge_bp = Blueprint("bridge", __name__)


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-Bridge")


@bridge_bp.route("", methods=["GET"])
def list_bridges():
    logger = _get_logger()
    managed_names = set(list_entities(current_app, "bridge"))

    # Discover system bridges and auto-create JSON definitions for unmanaged ones.
    discovered = NetBridge.discover_system_bridges(logger)
    for br in discovered:
        if br["name"] not in managed_names:
            _auto_create_bridge_json(br, logger)

    all_names = list_entities(current_app, "bridge")
    return jsonify({
        "success": True,
        "data": {"bridges": all_names}
    })


def _auto_create_bridge_json(br: dict, logger: logging.Logger):
    """Auto-create a JSON definition file for an unmanaged system bridge."""
    data = {
        "bridgeType": br["bridgeType"],
        "interfaces": br.get("interfaces", []),
    }
    if br.get("macAddress"):
        data["macAddress"] = br["macAddress"]
    write_entity_data(current_app, "bridge", br["name"], data)
    logger.info(f"Auto-created bridge definition [{br['name']}] type=[{br['bridgeType']}] "
                f"interfaces={data['interfaces']}")


@bridge_bp.route("", methods=["POST"])
def create_bridge():
    logger = _get_logger()
    if not request.is_json:
        return jsonify({
            "success": False,
            "error": {"code": "BAD_REQUEST", "message": "Content-Type must be application/json"}
        }), 400

    data = request.get_json()
    if data is None:
        return jsonify({
            "success": False,
            "error": {"code": "BAD_REQUEST", "message": "Request body is not valid JSON"}
        }), 400

    id = data.pop("id", None)
    if id is None:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "Missing 'id' field in request body"}
        }), 400

    bridge_path = get_entity_path(current_app, "bridge", id)
    already_exists = read_entity_data(current_app, "bridge", id) is not None

    write_entity_data(current_app, "bridge", id, data)
    logger.info(f"Create bridge [{id}] from [{bridge_path}]")

    bridge = NetBridge(logger, bridge_path)
    bridge.Process()

    status_code = 200 if already_exists else 201
    return jsonify({
        "success": True,
        "data": {"name": id}
    }), status_code


@bridge_bp.route("/<name>", methods=["GET"])
def get_bridge(name: str):
    data = read_entity_data(current_app, "bridge", name)
    if data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"Bridge '{name}' not found"}
        }), 404
    data["id"] = name
    return jsonify({
        "success": True,
        "data": data
    })


@bridge_bp.route("/<name>", methods=["DELETE"])
def delete_bridge(name: str):
    logger = _get_logger()
    data = read_entity_data(current_app, "bridge", name)
    if data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"Bridge '{name}' not found"}
        }), 404

    bridge_path = get_entity_path(current_app, "bridge", name)
    bridge = NetBridge(logger, bridge_path)
    bridge.Delete()

    delete_entity(current_app, "bridge", name)
    return jsonify({
        "success": True,
        "data": {"name": name, "deleted": True}
    })
