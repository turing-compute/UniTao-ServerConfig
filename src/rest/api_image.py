import logging
import os

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from rest.service import list_entities, read_entity_data, write_entity_data, get_entity_path, get_image_file_dir
from kvm.image.kvm_image import KvmImage

image_bp = Blueprint("image", __name__)


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-Image")


@image_bp.route("", methods=["GET"])
def list_images():
    # Images with JSON definitions.
    managed = set(list_entities(current_app, "image"))
    images = []
    for name in managed:
        images.append({"name": name, "managed": True})

    # Disk image files on disk without JSON definitions.
    image_dir = get_image_file_dir(current_app)
    if os.path.isdir(image_dir):
        for f in sorted(os.listdir(image_dir)):
            file_path = os.path.join(image_dir, f)
            if not os.path.isfile(file_path):
                continue
            name, ext = os.path.splitext(f)
            if ext not in (".qcow2", ".img", ".raw"):
                continue
            if name not in managed:
                images.append({"name": name, "file": f, "managed": False})

    return jsonify({
        "success": True,
        "data": {"images": images}
    })


@image_bp.route("", methods=["POST"])
def create_image():
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

    # If imagePath is not specified, auto-generate from imageFileDir.
    if "imagePath" not in data:
        image_format = data.get("imageFormat", "qcow2")
        ext = ".img" if image_format == "img" else ".qcow2"
        data["imagePath"] = os.path.join(get_image_file_dir(current_app), f"{entity_name}{ext}")

    image_path = get_entity_path(current_app, "image", entity_name)
    already_exists = read_entity_data(current_app, "image", entity_name) is not None

    write_entity_data(current_app, "image", entity_name, data)
    logger.info(f"Create image [{entity_name}] from [{image_path}]")

    image = KvmImage(image_path, logger)
    image.Create()

    status_code = 200 if already_exists else 201
    return jsonify({
        "success": True,
        "data": {"name": entity_name, "imagePath": image.ImagePath()}
    }), status_code


@image_bp.route("/<name>", methods=["GET"])
def get_image(name: str):
    data = read_entity_data(current_app, "image", name)
    if data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"Image '{name}' not found"}
        }), 404
    data["entityName"] = name
    return jsonify({
        "success": True,
        "data": data
    })
