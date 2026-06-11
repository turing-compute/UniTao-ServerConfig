import logging

from flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from rest.service import list_entities, read_entity_data, write_entity_data, delete_entity, get_entity_path
from network.bridge.net_bridge import NetBridge

bridge_bp = Blueprint("bridge", __name__)


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-Bridge")


@bridge_bp.route("", methods=["GET"])
def list_bridges():
    names = list_entities(current_app, "bridge")
    return jsonify({
        "success": True,
        "data": {"bridges": names}
    })


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

    entity_name = data.pop("entityName", None)
    if entity_name is None:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "Missing 'entityName' field in request body"}
        }), 400

    bridge_path = get_entity_path(current_app, "bridge", entity_name)
    already_exists = read_entity_data(current_app, "bridge", entity_name) is not None

    write_entity_data(current_app, "bridge", entity_name, data)
    logger.info(f"Create bridge [{entity_name}] from [{bridge_path}]")

    bridge = NetBridge(logger, bridge_path)
    bridge.Process()

    status_code = 200 if already_exists else 201
    return jsonify({
        "success": True,
        "data": {"name": entity_name}
    }), status_code


@bridge_bp.route("/<name>", methods=["GET"])
def get_bridge(name: str):
    data = read_entity_data(current_app, "bridge", name)
    if data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"Bridge '{name}' not found"}
        }), 404
    data["entityName"] = name
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
