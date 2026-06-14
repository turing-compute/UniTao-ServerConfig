import json
import logging
import os

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from shared.utilities import Util
from rest.service import list_entities, read_entity_data, write_entity_data, get_entity_path, get_image_file_dir, get_data_dir
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


def _get_qcow2_info(file_path: str, base_dir: str) -> dict:
    """Query qcow2 image info. Returns backing-file related fields, or empty dict on failure.

    base_dir: directory used to convert absolute backing path to relative.
    """
    try:
        result = Util.run_command(f"qemu-img info --output=json {file_path}")
        info = json.loads(result.stdout)
        virtual_bytes = info.get("virtual-size", 0)
        backing_path = info.get("full-backing-filename")
        # Convert to relative path if possible.
        if backing_path:
            try:
                backing_path = os.path.relpath(backing_path, base_dir)
            except ValueError:
                pass  # Keep absolute if on different drives.
        return {
            "sizeInGB": (virtual_bytes + 1024**3 - 1) // 1024**3,
            "baseImagePath": backing_path,
            "baseImageFormat": info.get("backing-filename-format"),
        }
    except (SystemError, json.JSONDecodeError, KeyError):
        return {}


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


def _create_image(id: str, data: dict):
    """Core image creation logic. id comes from URL path."""
    logger = _get_logger()

    # If imagePath is not specified, auto-generate relative to imageDataDir.
    if "imagePath" not in data:
        image_format = data.get("imageFormat", "qcow2")
        ext = ".img" if image_format == "img" else ".qcow2"
        abs_image_path = os.path.join(get_image_file_dir(current_app), f"{id}{ext}")
        try:
            data["imagePath"] = os.path.relpath(abs_image_path, get_data_dir(current_app, "image"))
        except ValueError:
            data["imagePath"] = abs_image_path

    image_path = get_entity_path(current_app, "image", id)
    prev_data = read_entity_data(current_app, "image", id)
    already_exists = prev_data is not None

    # If previous download failed, clean up partial file so KvmImage re-downloads.
    if prev_data and prev_data.get("downloadState") == "failed" and prev_data.get("imagePath"):
        prev_image_data_dir = get_data_dir(current_app, "image")
        prev_abs_image_path = os.path.normpath(os.path.join(prev_image_data_dir, prev_data["imagePath"]))
        if os.path.exists(prev_abs_image_path):
            os.remove(prev_abs_image_path)
            logger.info(f"Removed partial file [{prev_abs_image_path}] from failed download")

    # Mark in-progress before starting the potentially long operation.
    data["downloadState"] = "in-progress"
    write_entity_data(current_app, "image", id, data)
    logger.info(f"Create image [{id}] from [{image_path}]")

    image = KvmImage(image_path, logger)
    try:
        image.Create()
        download_state = "ready"
    except Exception:
        # Persist failed state before re-raising.
        persisted = read_entity_data(current_app, "image", id) or {}
        persisted["downloadState"] = "failed"
        write_entity_data(current_app, "image", id, persisted)
        raise

    # Persist ready state (KvmImage._save_data may have overwritten the file).
    persisted = read_entity_data(current_app, "image", id) or {}
    persisted["downloadState"] = download_state
    write_entity_data(current_app, "image", id, persisted)

    # Return imagePath relative to imageDataDir.
    image_data_dir = get_data_dir(current_app, "image")
    try:
        rel_image_path = os.path.relpath(image.ImagePath(), image_data_dir)
    except ValueError:
        rel_image_path = image.ImagePath()

    status_code = 200 if already_exists else 201
    return jsonify({
        "success": True,
        "data": {"name": id, "imagePath": rel_image_path, "downloadState": download_state}
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

    # Do NOT accept id in body when name is in URL.
    if "id" in data:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                       "message": "'id' must come from URL path, not request body"}
        }), 400

    return _create_image(name, data)


@image_bp.route("", methods=["POST"])
def create_image():
    """Create an image (backward-compatible). Entity name in request body."""
    data, err, code = _ensure_json_body()
    if data is None:
        return err, code

    id = data.pop("id", None)
    if id is None:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "Missing 'id' field in request body"}
        }), 400

    return _create_image(id, data)


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
        data["id"] = base_name
        data["managed"] = True
        # Convert imagePath to relative if stored as absolute.
        if data.get("imagePath") and os.path.isabs(data["imagePath"]):
            try:
                data["imagePath"] = os.path.relpath(
                    data["imagePath"], get_data_dir(current_app, "image"))
            except ValueError:
                pass
        # downloadState: from persisted data; fall back to file check.
        if "downloadState" not in data:
            if data.get("imagePath"):
                image_data_dir = get_data_dir(current_app, "image")
                abs_image_path = os.path.normpath(os.path.join(image_data_dir, data["imagePath"]))
                data["downloadState"] = "ready" if os.path.exists(abs_image_path) else "in-progress"
            else:
                data["downloadState"] = "in-progress"
        return jsonify({"success": True, "data": data})

    # Fallback: look for an unmanaged disk image file.
    image_dir = get_image_file_dir(current_app)
    if os.path.isdir(image_dir):
        for f in os.listdir(image_dir):
            base, ext = os.path.splitext(f)
            if ext in (".qcow2", ".img", ".raw") and base == base_name:
                file_path = os.path.join(image_dir, f)
                size = os.path.getsize(file_path)
                # Convert to relative path based on imageDataDir.
                try:
                    image_path = os.path.relpath(file_path, get_data_dir(current_app, "image"))
                except ValueError:
                    image_path = file_path
                result = {
                    "id": base_name,
                    "name": base_name,
                    "managed": False,
                    "file": f,
                    "imagePath": image_path,
                    "sizeInGB": (size + 1024**3 - 1) // 1024**3,
                }
                # Detect image format and backing file info (overwrites sizeInGB
                # with more accurate virtual-size when qemu-img info succeeds).
                qinfo = _get_qcow2_info(file_path, get_data_dir(current_app, "image"))
                if qinfo is not None:
                    result.update(qinfo)
                return jsonify({"success": True, "data": result})

    return jsonify({
        "success": False,
        "error": {"code": "NOT_FOUND", "message": f"Image '{name}' not found"}
    }), 404
