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
    managed_names = set(list_entities(current_app, "image"))
    images = []

    # Build a map of image filenames on disk.
    image_dir = get_image_file_dir(current_app)
    disk_files = {}  # name → filename
    if os.path.isdir(image_dir):
        for f in sorted(os.listdir(image_dir)):
            file_path = os.path.join(image_dir, f)
            if not os.path.isfile(file_path):
                continue
            name, ext = os.path.splitext(f)
            if ext in (".qcow2", ".img", ".raw"):
                disk_files[name] = f

    for name in managed_names:
        file_name = disk_files.pop(name, None)
        images.append({"name": name, "managed": True, "file": file_name})

    # Remaining disk files have no JSON definition.
    for name, file_name in sorted(disk_files.items()):
        images.append({"name": name, "managed": False, "file": file_name})

    return jsonify({
        "success": True,
        "data": {"images": images}
    })


def _ensure_json_body():
    """Validate Content-Type and parse JSON body. Returns (data, error_response)."""
    if not request.is_json:
        return None, jsonify({
            "success": False,
            "error": {"code": "BAD_REQUEST", "message": "Content-Type must be application/json"}
        }), 400
    data = request.get_json()
    if data is None:
        return None, jsonify({
            "success": False,
            "error": {"code": "BAD_REQUEST", "message": "Request body is not valid JSON"}
        }), 400
    return data, None, None


def _create_image(entity_name: str, data: dict):
    """Core image creation logic. entity_name comes from URL path."""
    logger = _get_logger()

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


@image_bp.route("/<name>", methods=["POST"])
def create_image_with_name(name: str):
    """Create an image. Entity name comes from the URL path.

    Request body (sample: plan/rest/data/download_image_sample.json):
        {
            "imageFormat": "qcow2",
            "imageSource": "remote",
            "downloadLink": "https://..."
        }
    """
    data, err, code = _ensure_json_body()
    if data is None:
        return err, code

    # Do NOT accept entityName in body when name is in URL.
    if "entityName" in data:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                       "message": "'entityName' must come from URL path, not request body"}
        }), 400

    return _create_image(name, data)


@image_bp.route("", methods=["POST"])
def create_image():
    """Create an image (backward-compatible). Entity name in request body."""
    data, err, code = _ensure_json_body()
    if data is None:
        return err, code

    entity_name = data.pop("entityName", None)
    if entity_name is None:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "Missing 'entityName' field in request body"}
        }), 400

    return _create_image(entity_name, data)


@image_bp.route("/<name>", methods=["GET"])
def get_image(name: str):
    # Strip known image extension from URL name if present.
    base_name = name
    for ext in (".qcow2", ".img", ".raw"):
        if name.endswith(ext):
            base_name = name[:-len(ext)]
            break

    # Check JSON definition first.
    data = read_entity_data(current_app, "image", base_name)
    if data is not None:
        data["entityName"] = base_name
        data["managed"] = True
        return jsonify({"success": True, "data": data})

    # Fallback: look for an unmanaged disk image file.
    image_dir = get_image_file_dir(current_app)
    if os.path.isdir(image_dir):
        for f in os.listdir(image_dir):
            base, ext = os.path.splitext(f)
            if ext in (".qcow2", ".img", ".raw") and base == base_name:
                file_path = os.path.join(image_dir, f)
                size = os.path.getsize(file_path)
                return jsonify({
                    "success": True,
                    "data": {
                        "entityName": base_name,
                        "name": base_name,
                        "managed": False,
                        "file": f,
                        "path": file_path,
                        "sizeBytes": size
                    }
                })

    return jsonify({
        "success": False,
        "error": {"code": "NOT_FOUND", "message": f"Image '{name}' not found"}
    }), 404
