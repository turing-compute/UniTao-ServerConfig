import json
import logging
import os
import random

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from shared.utilities import Util
from rest.service import (
    list_entities, read_entity_data, write_entity_data,
    vm_dir, vm_data_dir, vm_json_path, get_data_dir, get_image_file_dir,
)
from kvm.vm.kvm_vm import KvmVm

vm_bp = Blueprint("vm", __name__)


def _get_logger() -> logging.Logger:
    return Log.get_logger("REST-VM")


def _get_config() -> dict:
    return current_app.config["CONFIG"]


def _random_mac() -> str:
    mac = [random.randint(0, 255) for _ in range(5)]
    return "0E:" + ":".join(f"{num:02X}" for num in mac)


def _get_virsh_state(vm_name: str) -> str:
    try:
        result = Util.run_command("virsh list --name --all")
        if vm_name not in result.stdout_lines:
            return KvmVm.Keyword.VmStates.NotExists
        running_result = Util.run_command("virsh list --name")
        if vm_name in running_result.stdout_lines:
            return KvmVm.Keyword.VmStates.Running
        return KvmVm.Keyword.VmStates.Stopped
    except SystemError:
        return "unknown"


# ── JSON generators (simple params → full definition files) ──

def _gen_disk_json(name: str, os_size: int, config: dict) -> dict:
    vm_root = os.path.join(config["vmDataDir"], name)
    return {
        "imagePath": os.path.join(vm_root, f"{name}.qcow2"),
        "imageSource": "local",
        "imageFormat": "qcow2",
        "sizeInGB": os_size,
    }


def _gen_net_json(config: dict) -> dict:
    return {
        "ifaceType": "bridge",
        "bridgeName": config["defaultBridge"],
        "bridgeType": "linuxBridge",
        "macAddress": _random_mac(),
        "useDHCP4": True,
    }


def _gen_vm_json(name: str, cpu_number: int, os_name: str, os_size: int, config: dict) -> dict:
    vm_root = os.path.join(config["vmDataDir"], name)
    return {
        "vmPath": vm_root,
        "smp": cpu_number,
        "ramInGB": config["defaultRamInGB"],
        "disks": ["{vmPath}/data/disk1.json"],
        "networks": ["{vmPath}/data/net0.json"],
        "vmState": "running",
        "useCloudInit": True,
        "ciIsoPath": "{vmPath}/cloud_init.iso",
        "osType": "linux",
        "osVariant": os_name,
    }


# ── Routes ──

@vm_bp.route("", methods=["GET"])
def list_vms():
    names = list_entities(current_app, "vm")
    vm_list = []
    for name in names:
        virsh_state = _get_virsh_state(name)
        vm_list.append({"name": name, "virshState": virsh_state})
    return jsonify({
        "success": True,
        "data": {"vms": vm_list}
    })


@vm_bp.route("", methods=["POST"])
def create_vm():
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

    # Required fields.
    vm_id = data.get("vm_id", None)
    cpu_number = data.get("cpu_number", None)
    os_name = data.get("os_name", None)
    os_size = data.get("os_size", None)

    for field_name, field_val in [("vm_id", vm_id), ("cpu_number", cpu_number),
                                   ("os_name", os_name), ("os_size", os_size)]:
        if field_val is None:
            return jsonify({
                "success": False,
                "error": {"code": "VALIDATION_ERROR",
                          "message": f"Missing required field '{field_name}'"}
            }), 400

    if not isinstance(cpu_number, int) or cpu_number < 1:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "'cpu_number' must be a positive integer"}
        }), 400

    if not isinstance(os_size, int) or os_size < 1:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "'os_size' must be a positive integer (GB)"}
        }), 400

    config = _get_config()
    already_exists = read_entity_data(current_app, "vm", vm_id) is not None

    # Build directory structure: {vmDataDir}/{vm_id}/data/
    data_dir = vm_data_dir(current_app, vm_id)
    os.makedirs(data_dir, exist_ok=True)

    # Generate and write JSON definition files.
    disk_json = _gen_disk_json(vm_id, os_size, config)
    disk_path = os.path.join(data_dir, "disk1.json")
    with open(disk_path, "w") as f:
        json.dump(disk_json, f, indent=4)

    net_json = _gen_net_json(config)
    net_path = os.path.join(data_dir, "net0.json")
    with open(net_path, "w") as f:
        json.dump(net_json, f, indent=4)

    vm_json = _gen_vm_json(vm_id, cpu_number, os_name, os_size, config)
    vm_path = vm_json_path(current_app, vm_id)
    with open(vm_path, "w") as f:
        json.dump(vm_json, f, indent=4)

    # Preserve the original request for traceability.
    request_path = os.path.join(data_dir, "request.json")
    with open(request_path, "w") as f:
        json.dump(data, f, indent=4)

    logger.info(f"Create VM [{vm_id}] cpu={cpu_number} os={os_name} disk={os_size}GB")

    vm = KvmVm(logger, vm_path)
    vm.Process()

    status_code = 200 if already_exists else 201
    return jsonify({
        "success": True,
        "data": {"name": vm_id, "vmPath": vm_json["vmPath"]}
    }), status_code


@vm_bp.route("/<name>", methods=["GET"])
def get_vm(name: str):
    data = read_entity_data(current_app, "vm", name)
    if data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404
    virsh_state = _get_virsh_state(name)
    return jsonify({
        "success": True,
        "data": {
            "name": name,
            "definition": data,
            "virshState": virsh_state
        }
    })


@vm_bp.route("/<name>", methods=["DELETE"])
def delete_vm(name: str):
    logger = _get_logger()
    data = read_entity_data(current_app, "vm", name)
    if data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    data["vmState"] = KvmVm.Keyword.VmStates.NotExists
    write_entity_data(current_app, "vm", name, data)

    vm_path = vm_json_path(current_app, name)
    vm = KvmVm(logger, vm_path)
    vm.Process()

    vm_root = vm_dir(current_app, name)
    if os.path.isdir(vm_root):
        Util.run_command(f"rm -rf {vm_root}")

    return jsonify({
        "success": True,
        "data": {"name": name, "deleted": True}
    })


@vm_bp.route("/<name>/start", methods=["POST"])
def start_vm(name: str):
    logger = _get_logger()
    data = read_entity_data(current_app, "vm", name)
    if data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    data["vmState"] = KvmVm.Keyword.VmStates.Running
    write_entity_data(current_app, "vm", name, data)

    vm = KvmVm(logger, vm_json_path(current_app, name))
    vm.sync_vm_state()

    return jsonify({
        "success": True,
        "data": {"name": name, "vmState": KvmVm.Keyword.VmStates.Running}
    })


@vm_bp.route("/<name>/stop", methods=["POST"])
def stop_vm(name: str):
    logger = _get_logger()
    data = read_entity_data(current_app, "vm", name)
    if data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    data["vmState"] = KvmVm.Keyword.VmStates.Stopped
    write_entity_data(current_app, "vm", name, data)

    vm = KvmVm(logger, vm_json_path(current_app, name))
    vm.sync_vm_state()

    return jsonify({
        "success": True,
        "data": {"name": name, "vmState": KvmVm.Keyword.VmStates.Stopped}
    })
