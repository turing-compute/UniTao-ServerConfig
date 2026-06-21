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


def _get_host_key_dir() -> str:
    return _get_config().get("hostKeyDir", "/opt/unitiao/keys")


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

def _gen_disk_json(vm_name: str, base_image_data: dict, vm_root: str, logger: logging.Logger,
                    disk_size_gb: int = None) -> dict:
    """Generate a disk JSON that references an existing managed image as backing file."""
    base_image_path_rel = base_image_data.get("imagePath", "")
    image_format = base_image_data.get("imageFormat", "qcow2")
    disk_name = os.path.splitext(os.path.basename(base_image_path_rel))[0]
    disk_filename = f"{disk_name}.qcow2"
    disk_image_path = os.path.join(vm_root, disk_filename)

    # Resolve the backing image absolute path (needed for relpath below).
    abs_base_path = None
    if base_image_path_rel:
        image_data_dir = get_data_dir(current_app, "image")
        abs_base_path = os.path.normpath(os.path.join(image_data_dir, base_image_path_rel))

    # Use explicit diskSizeGB if provided, otherwise query base image virtual size.
    if disk_size_gb is not None and disk_size_gb > 0:
        size_gb = disk_size_gb
    else:
        size_gb = 20  # fallback default
        if abs_base_path is not None and os.path.exists(abs_base_path):
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
                 disk_file_name: str, net_file_name: str,
                 auth_type: str = None, customer_pwd: str = None,
                 customer_keys: list = None) -> dict:
    """Generate the main VM definition JSON per vm_data_sample.json."""
    vm_json = {
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
    }
    if auth_type:
        vm_json["authType"] = auth_type
    if customer_pwd:
        vm_json["customerPWD"] = customer_pwd
    if customer_keys:
        vm_json["customerKeys"] = customer_keys
    return vm_json


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
    bridge_name = data.get("bridge", None) or _get_config().get("defaultBridge", None)

    required = [
        ("id", vm_id), ("cpu", cpu), ("ramInGB", ram_gb),
        ("vmHostName", vm_host_name), ("osImage", os_image),
        ("osVariant", os_variant),
    ]
    for field_name, field_val in required:
        if field_val is None:
            return jsonify({
                "success": False,
                "error": {"code": "VALIDATION_ERROR",
                          "message": f"Missing required field '{field_name}'"}
            }), 400

    if not bridge_name:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": "No bridge specified and no defaultBridge in config"}
        }), 400

    # ── Optional fields ──
    ipv4 = data.get("ipv4", None)          # static IP in CIDR notation
    gateway4 = data.get("gateway4", None)  # gateway for static IP
    disk_size_gb = data.get("diskSizeGB", None)  # override disk size (reads base image size if omitted)
    auth_type = data.get("authType", None)
    customer_pwd = data.get("customerPWD", None)
    customer_keys = data.get("customerKeys", None)
    share_inv = data.get("shareInventoryData", False)
    prepare_domain_image = data.get("prepareDomainImage", False)

    # ── Validate authType ──
    if auth_type is not None:
        if auth_type not in KvmVm.Keyword.AuthTypes.list():
            return jsonify({
                "success": False,
                "error": {"code": "VALIDATION_ERROR",
                          "message": f"Invalid authType '{auth_type}', expected one of {KvmVm.Keyword.AuthTypes.list()}"}
            }), 400
        if auth_type == KvmVm.Keyword.AuthTypes.CustomerPWD:
            if not customer_pwd or not isinstance(customer_pwd, str):
                return jsonify({
                    "success": False,
                    "error": {"code": "VALIDATION_ERROR",
                              "message": "authType=CustomerPWD requires 'customerPWD' to be a non-empty string"}
                }), 400
        if auth_type == KvmVm.Keyword.AuthTypes.CustomerKey:
            if not customer_keys or not isinstance(customer_keys, list) or \
               not all(isinstance(k, str) and k for k in customer_keys):
                return jsonify({
                    "success": False,
                    "error": {"code": "VALIDATION_ERROR",
                              "message": "authType=CustomerKey requires 'customerKeys' to be a non-empty array of strings"}
                }), 400

    # ── Validate shareInventoryData ──
    if not isinstance(share_inv, bool):
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": "'shareInventoryData' must be a boolean"}
        }), 400

    # ── Validate prepareDomainImage ──
    if not isinstance(prepare_domain_image, bool):
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": "'prepareDomainImage' must be a boolean"}
        }), 400

    host_api_url = _get_config().get("hostApiUrl", None)

    # ── Validate diskSizeGB type ──
    if disk_size_gb is not None and (not isinstance(disk_size_gb, int) or disk_size_gb < 1):
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": "'diskSizeGB' must be a positive integer"}
        }), 400

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
    disk_json = _gen_disk_json(vm_id, base_image_data, vm_root, logger, disk_size_gb)
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
                           vm_root, disk_file_name, net_file_name,
                           auth_type=auth_type, customer_pwd=customer_pwd,
                           customer_keys=customer_keys)
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
    vm = KvmVm(logger, vm_path, key_dir=_get_host_key_dir(),
               auth_type=auth_type, customer_pwd=customer_pwd,
               customer_keys=customer_keys,
               share_inventory_data=share_inv, host_api_url=host_api_url,
               prepare_domain_image=prepare_domain_image)
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
        # Check if the VM exists in virsh (unmanaged).
        virsh_state = _get_virsh_state(name)
        if virsh_state == KvmVm.Keyword.VmStates.NotExists:
            return jsonify({
                "success": False,
                "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
            }), 404
        return jsonify({
            "success": True,
            "data": {"name": name, "virshState": virsh_state, "managed": False}
        })

    # Load all JSON files from the VM's data directory.
    data_dir = vm_data_dir(current_app, name)
    data_files = {}
    if os.path.isdir(data_dir):
        for f in sorted(os.listdir(data_dir)):
            if f.endswith(".json"):
                file_path = os.path.join(data_dir, f)
                with open(file_path, "r") as fh:
                    data_files[f] = json.load(fh)

    # Load inventory data from data/inventory/ subdirectory.
    inventory = {}
    inv_dir = os.path.join(data_dir, "inventory")
    if os.path.isdir(inv_dir):
        from datetime import datetime, timezone as tz
        for f in sorted(os.listdir(inv_dir)):
            if f.endswith(".json"):
                file_path = os.path.join(inv_dir, f)
                mtime = os.path.getmtime(file_path)
                ts = datetime.fromtimestamp(mtime, tz=tz.utc).isoformat()
                with open(file_path, "r") as fh:
                    inventory[f] = {"data": json.load(fh), "timestamp": ts}

    virsh_state = _get_virsh_state(name)
    result = {
        "name": name,
        "virshState": virsh_state,
    }
    result.update(data_files)
    if inventory:
        result["inventory"] = inventory
    return jsonify({
        "success": True,
        "data": result
    })


@vm_bp.route("/<name>/inventory", methods=["GET"])
def list_inventory(name: str):
    """List all inventory file names for a VM."""
    vm_def = read_entity_data(current_app, "vm", name)
    if vm_def is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    data_dir = vm_data_dir(current_app, name)
    inv_dir = os.path.join(data_dir, "inventory")
    files = []
    if os.path.isdir(inv_dir):
        files = sorted([f for f in os.listdir(inv_dir) if f.endswith(".json")])
    return jsonify({
        "success": True,
        "data": {"name": name, "files": files}
    })


@vm_bp.route("/<name>/inventory/<filename>", methods=["GET"])
def get_inventory_file(name: str, filename: str):
    """Get a specific inventory file for a VM."""
    vm_def = read_entity_data(current_app, "vm", name)
    if vm_def is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    data_dir = vm_data_dir(current_app, name)
    inv_file = os.path.join(data_dir, "inventory", filename)
    if not os.path.isfile(inv_file):
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"File '{filename}' not found"}
        }), 404

    with open(inv_file, "r") as f:
        content = json.load(f)
    from datetime import datetime, timezone as tz
    mtime = os.path.getmtime(inv_file)
    ts = datetime.fromtimestamp(mtime, tz=tz.utc).isoformat()
    return jsonify({
        "success": True,
        "data": {"name": name, "file": filename, "content": content, "timestamp": ts}
    })


@vm_bp.route("/<name>/inventory", methods=["POST"])
def post_inventory(name: str):
    """Accept inventory data from a VM and store it in data/inventory/."""
    logger = _get_logger()
    vm_def = read_entity_data(current_app, "vm", name)
    if vm_def is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    if not request.is_json:
        return jsonify({
            "success": False,
            "error": {"code": "BAD_REQUEST", "message": "Content-Type must be application/json"}
        }), 400

    inv_data = request.get_json()
    if inv_data is None:
        return jsonify({
            "success": False,
            "error": {"code": "BAD_REQUEST", "message": "Request body is not valid JSON"}
        }), 400

    data_dir = vm_data_dir(current_app, name)
    inv_dir = os.path.join(data_dir, "inventory")
    os.makedirs(inv_dir, exist_ok=True)

    # Use "name" field from body as filename if present; otherwise timestamp.
    file_name = inv_data.get("name", None)
    if file_name and isinstance(file_name, str) and file_name.strip():
        # Sanitize: reject names with path separators.
        safe_name = file_name.strip()
        if "/" in safe_name or "\\" in safe_name:
            return jsonify({
                "success": False,
                "error": {"code": "VALIDATION_ERROR",
                          "message": f"Invalid 'name' field: '{safe_name}' contains path separators"}
            }), 400
        inv_file = os.path.join(inv_dir, f"{safe_name}.json")
    else:
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        inv_file = os.path.join(inv_dir, f"{timestamp}.json")

    with open(inv_file, "w") as f:
        json.dump(inv_data, f, indent=4)

    stored_name = os.path.basename(inv_file)
    logger.info(f"Inventory data posted for VM [{name}] → {stored_name}")
    return jsonify({
        "success": True,
        "data": {"name": name, "file": stored_name}
    }), 201


@vm_bp.route("/<name>", methods=["DELETE"])
def delete_vm(name: str):
    logger = _get_logger()
    data = read_entity_data(current_app, "vm", name)

    # Managed VM: full cleanup (virsh destroy + remove data dir).
    if data is not None:
        data["vmState"] = KvmVm.Keyword.VmStates.NotExists
        write_entity_data(current_app, "vm", name, data)
        vm_path = vm_json_path(current_app, name)
        vm = KvmVm(logger, vm_path, key_dir=_get_host_key_dir())
        vm.Process()
        vm_root = vm_dir(current_app, name)
        if os.path.isdir(vm_root):
            Util.run_command(f"rm -rf {vm_root}")
        return jsonify({
            "success": True,
            "data": {"name": name, "deleted": True, "managed": True}
        })

    # Unmanaged VM: just destroy via virsh.
    virsh_state = _get_virsh_state(name)
    if virsh_state == KvmVm.Keyword.VmStates.NotExists:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    logger.info(f"Destroying unmanaged VM [{name}] (virsh state: {virsh_state})")
    if virsh_state == KvmVm.Keyword.VmStates.Running:
        Util.run_command(f"virsh destroy {name}")
    # Undefine to remove from virsh (ignore if already gone).
    try:
        Util.run_command(f"virsh undefine {name}")
    except SystemError:
        pass

    return jsonify({
        "success": True,
        "data": {"name": name, "deleted": True, "managed": False}
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

    vm = KvmVm(logger, vm_json_path(current_app, name), key_dir=_get_host_key_dir())
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

    vm = KvmVm(logger, vm_json_path(current_app, name), key_dir=_get_host_key_dir())
    vm.Process()

    return jsonify({
        "success": True,
        "data": {"name": name, "vmState": KvmVm.Keyword.VmStates.Stopped}
    })


@vm_bp.route("/<name>/commit", methods=["POST"])
def commit_vm_image(name: str):
    """Commit a VM's qcow2 disk changes back to its backing base image.

    Request body (optional):
        {"disk": 0}            — disk index (0=first), default 0
        {"disk": "disk-ubuntu.json"}  — disk definition filename

    Prerequisites:
      - vmState == "stopped"
      - VM must not exist in virsh (destroyed)
    The backing image is auto-detected from the VM's qcow2 disk.
    """
    logger = _get_logger()

    # Parse request body for disk selection.
    disk_selector = 0  # default: first disk
    if request.is_json:
        body = request.get_json()
        if body and "disk" in body:
            disk_selector = body["disk"]

    # 1. Verify VM exists and vmState == "stopped".
    vm_data = read_entity_data(current_app, "vm", name)
    if vm_data is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    if vm_data.get("vmState") != KvmVm.Keyword.VmStates.Stopped:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": f"vmState must be 'stopped', got '{vm_data.get('vmState')}'"}
        }), 400

    # 2. Verify VM is destroyed in virsh.
    try:
        result = Util.run_command("virsh list --name --all")
        if name in result.stdout_lines:
            return jsonify({
                "success": False,
                "error": {"code": "VALIDATION_ERROR",
                          "message": f"VM '{name}' still exists in virsh. "
                                     f"Run 'virsh destroy {name}' first."}
            }), 400
    except SystemError:
        pass

    # 3. Resolve disk.
    disks = vm_data.get("disks", [])
    if not disks:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": f"VM '{name}' has no disks defined"}
        }), 400

    # Resolve by index or by filename.
    if isinstance(disk_selector, int):
        if disk_selector < 0 or disk_selector >= len(disks):
            return jsonify({
                "success": False,
                "error": {"code": "VALIDATION_ERROR",
                          "message": f"disk index {disk_selector} out of range "
                                     f"(VM has {len(disks)} disk(s))"}
            }), 400
        disk_ref = disks[disk_selector]
    else:
        disk_ref = str(disk_selector)
        # Allow matching by filename (e.g. "disk-ubuntu.json").
        matched = [d for d in disks if os.path.basename(d) == disk_ref]
        if not matched:
            return jsonify({
                "success": False,
                "error": {"code": "VALIDATION_ERROR",
                          "message": f"disk '{disk_ref}' not found in VM disks"}
            }), 400
        disk_ref = matched[0]

    # Parse disk reference (supports {vmPath} placeholder and relative paths).
    vm_path = vm_data.get("vmPath", vm_dir(current_app, name))
    if disk_ref.startswith("{vmPath}"):
        disk_ref = disk_ref.replace("{vmPath}", vm_path, 1)
    if not os.path.isabs(disk_ref):
        disk_ref = os.path.join(vm_data_dir(current_app, name), disk_ref)
    disk_json_path = os.path.normpath(disk_ref)

    if not os.path.isfile(disk_json_path):
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND",
                      "message": f"Disk definition not found: {disk_json_path}"}
        }), 404

    with open(disk_json_path, "r") as f:
        disk_data = json.load(f)

    disk_image_rel = disk_data.get("imagePath")
    if not disk_image_rel:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "Disk missing 'imagePath'"}
        }), 400

    # Resolve disk image path relative to the disk JSON's own directory.
    disk_image_abs = os.path.normpath(os.path.join(os.path.dirname(disk_json_path), disk_image_rel))

    if not os.path.isfile(disk_image_abs):
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND",
                      "message": f"Disk image file not found: {disk_image_abs}"}
        }), 404

    # 4. Detect backing image from qemu-img info.
    try:
        qemu_info = Util.run_command(f"qemu-img info --output=json {disk_image_abs}")
        qemu_data = json.loads(qemu_info.stdout)
        backing_path = qemu_data.get("full-backing-filename", "")
    except (SystemError, json.JSONDecodeError) as e:
        return jsonify({
            "success": False,
            "error": {"code": "SYSTEM_COMMAND_FAILED",
                      "message": f"Failed to query disk info: {e}"}
        }), 500

    if not backing_path:
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR",
                      "message": "Disk has no backing file to commit to"}
        }), 400

    # 5. Resolve backing image name from the filesystem.
    backing_abs = os.path.normpath(backing_path)
    image_name = os.path.splitext(os.path.basename(backing_abs))[0]

    # Look up managed image.
    image_data = read_entity_data(current_app, "image", image_name)

    # 6. Commit disk changes to backing image.
    logger.info(f"Committing VM [{name}] disk [{disk_image_abs}] → image [{image_name}]")
    try:
        Util.run_command(f"qemu-img commit {disk_image_abs}")
    except SystemError as e:
        return jsonify({
            "success": False,
            "error": {"code": "SYSTEM_COMMAND_FAILED",
                      "message": f"qemu-img commit failed: {e}"}
        }), 500

    # 7. Update backing image metadata if managed.
    if image_data is not None:
        image_data["lastCommitFrom"] = name
        from datetime import datetime, timezone
        image_data["lastCommitAt"] = datetime.now(timezone.utc).isoformat()
        write_entity_data(current_app, "image", image_name, image_data)

    logger.info(f"VM [{name}] committed to image [{image_name}] successfully")
    return jsonify({
        "success": True,
        "data": {
            "vmName": name,
            "imageName": image_name,
            "diskPath": disk_image_abs,
            "message": f"VM '{name}' changes committed to image '{image_name}'"
        }
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

    vm = KvmVm(logger, vm_json_path(current_app, vm_id), key_dir=_get_host_key_dir())
    vm.Process()

    logger.info(f"Patch VM [{vm_id}] vmState → {vm_state}")
    return jsonify({
        "success": True,
        "data": {"name": vm_id, "vmState": vm_state}
    })
