import json
import logging
import os
import random

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from shared.utilities import Util
from rest.service import (
    list_entities, read_entity_data, write_entity_data, delete_entity,
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


# ── JSON generators (POST params → definition files per vm_data_sample.json) ──

def _gen_disk_json(vm_name: str, base_image_data: dict, vm_root: str, logger: logging.Logger) -> dict:
    """Generate a disk JSON that references an existing managed image as backing file."""
    base_image_path_rel = base_image_data.get("imagePath", "")
    image_format = base_image_data.get("imageFormat", "qcow2")
    disk_name = os.path.splitext(os.path.basename(base_image_path_rel))[0]
    disk_filename = f"{disk_name}.qcow2"
    disk_image_path = os.path.join(vm_root, disk_filename)

    # Try to read virtual size from the base image file.
    size_gb = 20  # fallback default
    if base_image_path_rel:
        image_data_dir = get_data_dir(current_app, "image")
        abs_base_path = os.path.normpath(os.path.join(image_data_dir, base_image_path_rel))
        if os.path.exists(abs_base_path):
            size_gb = _query_virtual_size_gb(abs_base_path, logger)

    # Store paths relative to the disk JSON's own directory.
    data_dir = os.path.join(vm_root, "data")
    try:
        image_path_rel = os.path.relpath(disk_image_path, data_dir)
    except ValueError:
        image_path_rel = disk_image_path
    try:
        base_rel = os.path.relpath(abs_base_path, data_dir) if base_image_path_rel else base_image_path_rel
    except (ValueError, UnboundLocalError):
        base_rel = base_image_path_rel

    return {
        "imagePath": image_path_rel,
        "imageSource": "local",
        "imageFormat": image_format,
        "sizeInGB": size_gb,
        "baseImagePath": base_rel,
        "baseImageFormat": image_format,
    }


def _gen_net_json(bridge_name: str, bridge_data: dict, static_ip4: str = None, gateway4: str = None) -> dict:
    """Generate a network interface JSON connected to a managed bridge."""
    use_dhcp = static_ip4 is None
    net = {
        "ifaceType": "bridge",
        "bridgeName": bridge_name,
        "bridgeType": bridge_data.get("bridgeType", "linuxBridge"),
        "macAddress": _random_mac(),
        "useDHCP4": use_dhcp,
    }
    if not use_dhcp:
        net["ip4"] = static_ip4
        if gateway4:
            net["gateway4"] = gateway4
    return net


def _gen_vm_json(vm_name: str, cpu: int, ram_gb: int, os_variant: str,
                 vm_host_name: str, vm_root: str,
                 disk_file_name: str, net_file_name: str) -> dict:
    """Generate the main VM definition JSON per vm_data_sample.json."""
    return {
        "id": vm_name,
        "description": [],
        "vmPath": vm_root,
        "smp": cpu,
        "ramInGB": ram_gb,
        "disks": ["{vmPath}/data/" + disk_file_name],
        "networks": ["{vmPath}/data/" + net_file_name],
        "vmState": "stopped",
        "useCloudInit": True,
        "ciIsoPath": "{vmPath}/cloud_init.iso",
        "vmHostName": vm_host_name,
        "osType": "linux",
        "osVariant": os_variant,
        "defaultPWD": "ubuntu",
    }


def _query_virtual_size_gb(image_path: str, logger: logging.Logger) -> int:
    """Query virtual disk size in GB from an existing image file (round up)."""
    try:
        result = Util.run_command(f"qemu-img info --output=json {image_path}")
        info = json.loads(result.stdout)
        virtual_bytes = info.get("virtual-size", 0)
        return max(1, (virtual_bytes + 1024**3 - 1) // 1024**3)
    except Exception:
        logger.warning(f"Cannot query virtual size for [{image_path}], using fallback")
        return 20


# ── Routes ──

@vm_bp.route("", methods=["GET"])
def list_vms():
    # VMs with JSON definitions in the data directory.
    managed = set(list_entities(current_app, "vm"))
    vm_list = []
    for name in managed:
        virsh_state = _get_virsh_state(name)
        vm_list.append({"name": name, "virshState": virsh_state, "managed": True})

    # VMs known to virsh but not yet managed by REST.
    try:
        result = Util.run_command("virsh list --name --all")
        for name in result.stdout_lines:
            if name and name not in managed:
                vm_list.append({"name": name, "virshState": _get_virsh_state(name), "managed": False})
    except SystemError:
        pass

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

    # ── Required fields ──
    vm_id = data.get("id", None)
    cpu = data.get("cpu", None)
    ram_gb = data.get("ramInGB", None)
    vm_host_name = data.get("vmHostName", None)
    os_image = data.get("osImage", None)
    os_variant = data.get("osVariant", None)
    bridge_name = data.get("bridge", None)

    required = [
        ("id", vm_id), ("cpu", cpu), ("ramInGB", ram_gb),
        ("vmHostName", vm_host_name), ("osImage", os_image),
        ("osVariant", os_variant), ("bridge", bridge_name),
    ]
    for field_name, field_val in required:
        if field_val is None:
            return jsonify({
                "success": False,
                "error": {"code": "VALIDATION_ERROR",
                          "message": f"Missing required field '{field_name}'"}
            }), 400

    # ── Optional fields ──
    ipv4 = data.get("ipv4", None)          # static IP in CIDR notation
    gateway4 = data.get("gateway4", None)  # gateway for static IP

    # ── Validate types ──
    for field_name, field_val in [("cpu", cpu), ("ramInGB", ram_gb)]:
        if not isinstance(field_val, int) or field_val < 1:
            return jsonify({
                "success": False,
                "error": {"code": "VALIDATION_ERROR",
                          "message": f"'{field_name}' must be a positive integer"}
            }), 400

    # ── Resolve referenced image ──
    base_image_data = read_entity_data(current_app, "image", os_image)
    if base_image_data is None:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": f"Referenced osImage '{os_image}' not found in managed images"}
        }), 400
    if base_image_data.get("downloadState") != "ready":
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": f"osImage '{os_image}' is not ready (state: {base_image_data.get('downloadState')})"}
        }), 400

    # ── Resolve referenced bridge ──
    bridge_data = read_entity_data(current_app, "bridge", bridge_name)
    if bridge_data is None:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": f"Referenced bridge '{bridge_name}' not found in managed bridges"}
        }), 400

    # ── Build directory structure ──
    already_exists = read_entity_data(current_app, "vm", vm_id) is not None
    vm_root = vm_dir(current_app, vm_id)
    data_dir = vm_data_dir(current_app, vm_id)
    os.makedirs(data_dir, exist_ok=True)

    # ── Generate definition files ──
    # Disk JSON (named after the base image).
    disk_json = _gen_disk_json(vm_id, base_image_data, vm_root, logger)
    disk_base = os.path.splitext(os.path.basename(base_image_data.get("imagePath", os_image)))[0]
    disk_file_name = f"disk-{disk_base}.json"
    disk_path = os.path.join(data_dir, disk_file_name)
    with open(disk_path, "w") as f:
        json.dump(disk_json, f, indent=4)

    # Network JSON (named after the bridge).
    net_json = _gen_net_json(bridge_name, bridge_data, ipv4, gateway4)
    net_file_name = f"net-{bridge_name}.json"
    net_path = os.path.join(data_dir, net_file_name)
    with open(net_path, "w") as f:
        json.dump(net_json, f, indent=4)

    # Main VM JSON.
    vm_json = _gen_vm_json(vm_id, cpu, ram_gb, os_variant, vm_host_name,
                           vm_root, disk_file_name, net_file_name)
    vm_path = vm_json_path(current_app, vm_id)
    with open(vm_path, "w") as f:
        json.dump(vm_json, f, indent=4)

    # Preserve the original request for traceability.
    request_path = os.path.join(data_dir, "request.json")
    with open(request_path, "w") as f:
        json.dump(data, f, indent=4)

    logger.info(f"Create VM [{vm_id}] cpu={cpu} ram={ram_gb}GB "
                f"image={os_image} bridge={bridge_name} os={os_variant} host={vm_host_name}")

    # ── Process (creates virsh VM) ──
    vm = KvmVm(logger, vm_path)
    vm.Process()

    status_code = 200 if already_exists else 201
    return jsonify({
        "success": True,
        "data": {"name": vm_id, "vmPath": vm_json["vmPath"]}
    }), status_code


@vm_bp.route("/<name>", methods=["GET"])
def get_vm(name: str):
    vm_def = read_entity_data(current_app, "vm", name)
    if vm_def is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    # Load all JSON files from the VM's data directory.
    data_dir = vm_data_dir(current_app, name)
    data_files = {}
    if os.path.isdir(data_dir):
        for f in sorted(os.listdir(data_dir)):
            if f.endswith(".json"):
                file_path = os.path.join(data_dir, f)
                with open(file_path, "r") as fh:
                    data_files[f] = json.load(fh)

    virsh_state = _get_virsh_state(name)
    result = {
        "name": name,
        "virshState": virsh_state,
    }
    result.update(data_files)
    return jsonify({
        "success": True,
        "data": result
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
    vm.Process()

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
    vm.Process()

    return jsonify({
        "success": True,
        "data": {"name": name, "vmState": KvmVm.Keyword.VmStates.Stopped}
    })


@vm_bp.route("", methods=["PATCH"])
def patch_vm():
    """Patch a VM's state (start/stop). Request body per vm_patch_sample.json."""
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

    vm_id = data.get("id", None)
    vm_state = data.get("vmState", None)

    if vm_id is None:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "Missing required field 'id'"}
        }), 400
    if vm_state is None:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "Missing required field 'vmState'"}
        }), 400
    if vm_state not in (KvmVm.Keyword.VmStates.Running, KvmVm.Keyword.VmStates.Stopped):
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": f"'vmState' must be '{KvmVm.Keyword.VmStates.Running}' or '{KvmVm.Keyword.VmStates.Stopped}'"}
        }), 400

    vm_data = read_entity_data(current_app, "vm", vm_id)
    if vm_data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{vm_id}' not found"}
        }), 404

    vm_data["vmState"] = vm_state
    write_entity_data(current_app, "vm", vm_id, vm_data)

    vm = KvmVm(logger, vm_json_path(current_app, vm_id))
    vm.Process()

    logger.info(f"Patch VM [{vm_id}] vmState → {vm_state}")
    return jsonify({
        "success": True,
        "data": {"name": vm_id, "vmState": vm_state}
    })
