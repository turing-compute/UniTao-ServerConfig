import ipaddress
import json
import logging
import os
import random
import tempfile
import threading

from extlib.flask import Blueprint, request, jsonify, current_app

from shared.logger import Log
from shared.utilities import Util
from rest.service import (
    list_entities, read_entity_data, write_entity_data, delete_entity,
    vm_dir, vm_data_dir, vm_json_path, get_data_dir, get_image_file_dir,
)
from rest.schema_loader import (
    load_vm_schemas, check_vm_schema_consistency,
    artifact_paths_for_changes, remove_artifacts,
)
from kvm.vm.kvm_vm import KvmVm

vm_bp = Blueprint("vm", __name__)

# Per-VM lock: serialize PATCH against start/stop/delete for the same VM.
_vm_locks: dict = {}
_vm_locks_guard = threading.Lock()


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


def _state_matches(desired_state: str, live_state: str) -> bool:
    """Compare data-file desired state with live virsh state.

    Managed VMs are created with `virsh create` (transient domains), so a
    stopped VM has no domain at all: notExists matches desired=stopped.
    """
    if live_state == "unknown":
        return False
    if desired_state == KvmVm.Keyword.VmStates.Running:
        return live_state == KvmVm.Keyword.VmStates.Running
    if desired_state == KvmVm.Keyword.VmStates.Stopped:
        return live_state != KvmVm.Keyword.VmStates.Running
    if desired_state == KvmVm.Keyword.VmStates.NotExists:
        return live_state == KvmVm.Keyword.VmStates.NotExists
    return False


# ── PATCH helpers ──

def _get_vm_lock(vm_name: str) -> threading.Lock:
    with _vm_locks_guard:
        return _vm_locks.setdefault(vm_name, threading.Lock())


def _patch_error(code: str, message: str, status: int, details: dict = None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return jsonify({"success": False, "error": error}), status


_PATCH_TYPE_CHECKS = {
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def _validate_patch_value(field: str, value, rule: dict):
    """Validate a single PATCH value against its rest.json rule.

    Returns an error message, or None when valid. Only first-level scalar
    fields are supported; array/object rules exist for future use.
    """
    expected_type = rule.get("type")
    if expected_type is not None:
        types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_PATCH_TYPE_CHECKS.get(t, lambda _v: False)(value) for t in types):
            return f"'{field}' must be of type {types}, got {type(value).__name__}"

    enum = rule.get("enum")
    if enum is not None and value not in enum:
        return f"'{field}' must be one of {enum}, got {value!r}"

    if isinstance(value, int) and not isinstance(value, bool):
        minimum = rule.get("minimum")
        if minimum is not None and value < minimum:
            return f"'{field}' must be >= {minimum}, got {value}"

    if isinstance(value, str):
        min_length = rule.get("minLength")
        if min_length is not None and len(value) < min_length:
            return f"'{field}' must have length >= {min_length}"

    if isinstance(value, list):
        min_items = rule.get("minItems")
        if min_items is not None and len(value) < min_items:
            return f"'{field}' must contain at least {min_items} item(s)"
        item_rule = rule.get("items")
        if item_rule:
            for idx, item in enumerate(value):
                error = _validate_patch_value(f"{field}[{idx}]", item, item_rule)
                if error:
                    return error

    fmt = rule.get("format")
    if fmt == "ipv4-cidr" and value is not None:
        try:
            ipaddress.IPv4Interface(value)
        except ValueError:
            return f"'{field}' must be an IPv4 CIDR (e.g. 192.168.1.10/24)"
    elif fmt == "ipv4" and value is not None:
        try:
            ipaddress.IPv4Address(value)
        except ValueError:
            return f"'{field}' must be an IPv4 address"

    return None


def _resolve_vm_path(vm_name: str, vm_data: dict) -> str:
    """Resolve vm JSON vmPath to an absolute path (same rule as KvmVm)."""
    vm_path = vm_data.get("vmPath") or vm_dir(current_app, vm_name)
    if not os.path.isabs(vm_path):
        vm_path = Util.abs_path(vm_data_dir(current_app, vm_name), vm_path)
    return os.path.normpath(vm_path)


def _resolve_vm_ref(vm_name: str, vm_path: str, ref: str) -> str:
    """Resolve a {vmPath} placeholder or VM-data-relative reference."""
    if ref.startswith("{vmPath}"):
        ref = ref.replace("{vmPath}", vm_path, 1)
    if not os.path.isabs(ref):
        ref = Util.abs_path(vm_data_dir(current_app, vm_name), ref)
    return os.path.normpath(ref)


def _resolve_ci_iso_path(vm_name: str, vm_data: dict, vm_path: str) -> str:
    ci_path = vm_data.get("ciIsoPath") or os.path.join(vm_path, "cloud_init.iso")
    return _resolve_vm_ref(vm_name, vm_path, ci_path)


def _read_text(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def _atomic_write_json(path: str, data: dict):
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".patch-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _restore_files(backups: dict, logger: logging.Logger):
    for path, content in backups.items():
        try:
            with open(path, "w") as f:
                f.write(content)
        except OSError as e:
            logger.error("Failed to restore [%s]: %s", path, e)


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


@vm_bp.route("/<name>/inventory/<filename>", methods=["PATCH"])
def patch_inventory(name: str, filename: str):
    """Patch an inventory file — JSON deep-merge the request body into the existing content."""
    logger = _get_logger()
    vm_def = read_entity_data(current_app, "vm", name)
    if vm_def is None:
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"VM '{name}' not found"}
        }), 404

    inv_file = os.path.join(vm_data_dir(current_app, name), "inventory", filename)
    if not os.path.isfile(inv_file):
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": f"Inventory file '{filename}' not found"}
        }), 404

    if not request.is_json:
        return jsonify({
            "success": False,
            "error": {"code": "BAD_REQUEST", "message": "Content-Type must be application/json"}
        }), 400

    patch_data = request.get_json()
    if patch_data is None:
        return jsonify({
            "success": False,
            "error": {"code": "BAD_REQUEST", "message": "Request body is not valid JSON"}
        }), 400

    with open(inv_file, "r") as f:
        existing = json.load(f)

    _deep_merge(existing, patch_data)

    with open(inv_file, "w") as f:
        json.dump(existing, f, indent=4)

    logger.info(f"Inventory file [{filename}] patched for VM [{name}]")
    return jsonify({
        "success": True,
        "data": {"name": name, "file": filename}
    })


def _deep_merge(base: dict, patch: dict):
    """Recursively merge patch into base in-place."""
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


@vm_bp.route("/<name>", methods=["PATCH"])
def patch_vm(name: str):
    """Partially update a stopped VM (cpu, ramInGB, bridge).

    Allowed fields, validation rules and target mappings are driven by
    src/data/schema/vms/{rest,rest_map,data_map}.json.
    """
    logger = _get_logger()

    if not request.is_json:
        return _patch_error(
            "BAD_REQUEST", "Content-Type must be application/json", 400)

    body = request.get_json()
    if not isinstance(body, dict):
        return _patch_error(
            "BAD_REQUEST", "Request body must be a JSON object", 400)

    try:
        rest_schema, rest_map, data_map = load_vm_schemas()
    except RuntimeError as e:
        logger.error("Cannot load vms schema: %s", e)
        return _patch_error(
            "INTERNAL_ERROR", "VM schema is not available", 500)

    for warning in check_vm_schema_consistency(rest_schema, rest_map, data_map):
        logger.warning("Schema inconsistency: %s", warning)

    patch_schema = rest_schema.get("patch", {})
    allowed_fields = patch_schema.get("allowedFields", {})
    immutable_fields = patch_schema.get("immutableFields", {})

    with _get_vm_lock(name):
        vm_data = read_entity_data(current_app, "vm", name)
        if vm_data is None:
            return _patch_error("NOT_FOUND", f"VM '{name}' not found", 404)

        # PATCH permission is based on the desired state in the data file.
        desired_state = vm_data.get("vmState")
        if desired_state == KvmVm.Keyword.VmStates.NotExists:
            return _patch_error(
                "VM_NOT_EXISTS", f"VM '{name}' is marked as notExists", 409)
        if desired_state not in (KvmVm.Keyword.VmStates.Running,
                                 KvmVm.Keyword.VmStates.Stopped):
            return _patch_error(
                "INTERNAL_ERROR",
                f"VM '{name}' has an invalid vmState '{desired_state}'", 500)

        if not body:
            return _patch_error(
                "EMPTY_PATCH", "Request body has no PATCH fields", 400)

        unknown = sorted(field for field in body if field not in allowed_fields)
        if unknown:
            return _patch_error(
                "INVALID_PATCH_FIELD",
                f"Unsupported PATCH field(s): {unknown}",
                400,
                {
                    "allowedFields": sorted(allowed_fields.keys()),
                    "immutableFields": sorted(immutable_fields.keys()),
                    "schemaUrl": "/api/v1/schema",
                })

        # ── Validate values ──
        bridge_entities = {}
        for field, value in body.items():
            error = _validate_patch_value(field, value, allowed_fields[field])
            if error:
                return _patch_error("VALIDATION_ERROR", error, 400)
            if allowed_fields[field].get("format") == "managedBridge":
                bridge_data = read_entity_data(current_app, "bridge", value)
                if bridge_data is None:
                    return _patch_error(
                        "VALIDATION_ERROR",
                        f"Referenced bridge '{value}' not found in managed bridges",
                        400)
                bridge_entities[value] = bridge_data

        # ── Resolve the single network definition (current REST API limit) ──
        networks = vm_data.get("networks", [])
        if not isinstance(networks, list) or len(networks) != 1:
            count = len(networks) if isinstance(networks, list) else 0
            return _patch_error(
                "NOT_IMPLEMENTED",
                f"PATCH is only implemented for VMs with exactly one network: "
                f"VM '{name}' has {count} entries in networks[]",
                501)
        if not isinstance(networks[0], str) or not networks[0]:
            return _patch_error(
                "INTERNAL_ERROR",
                f"VM '{name}' has an invalid networks[0] reference", 500)

        vm_path = _resolve_vm_path(name, vm_data)
        net_path = _resolve_vm_ref(name, vm_path, networks[0])
        if not os.path.isfile(net_path):
            return _patch_error(
                "INTERNAL_ERROR",
                f"Network definition not found: {net_path}", 500)
        net_data = Util.read_json_file(net_path)

        # ── Reconcile desired state with live state, then check permission ──
        # The request is fully validated before this point, so a malformed
        # PATCH never changes the VM's live state.
        live_state = _get_virsh_state(name)
        if live_state == "unknown":
            return _patch_error(
                "SYSTEM_COMMAND_FAILED",
                f"Cannot determine virsh state for VM '{name}'", 500)

        if not _state_matches(desired_state, live_state):
            logger.info(
                "VM [%s] desired state [%s] != live state [%s], reconciling",
                name, desired_state, live_state)
            try:
                KvmVm(logger, vm_json_path(current_app, name),
                      key_dir=_get_host_key_dir()).Process()
            except Exception as e:
                logger.error("Failed to reconcile VM [%s] state: %s", name, e)
                return _patch_error(
                    "SYSTEM_COMMAND_FAILED",
                    f"Failed to reconcile VM '{name}' state: {e}", 500)

            live_state = _get_virsh_state(name)
            if not _state_matches(desired_state, live_state):
                return _patch_error(
                    "SYSTEM_COMMAND_FAILED",
                    f"Failed to reconcile VM '{name}': desired state "
                    f"'{desired_state}', live state '{live_state}'", 500)

        if desired_state == KvmVm.Keyword.VmStates.Running:
            return _patch_error(
                "VM_RUNNING",
                f"VM '{name}' desired state is running; "
                f"stop it before applying PATCH", 409)

        # ── Compute updates (first-level fields only) ──
        field_map = rest_map.get("fields", {})
        vm_updates = {}
        net_updates = {}
        changed_vm_paths = []
        changed_net_paths = []
        updated_fields = []

        for field, value in body.items():
            mapping = field_map.get(field)
            if mapping is None:
                return _patch_error(
                    "INTERNAL_ERROR",
                    f"No rest_map mapping for PATCH field '{field}'", 500)

            target_path = mapping["path"]
            if mapping["file"] == "vm":
                if vm_data.get(target_path) != value:
                    vm_updates[target_path] = value
                    changed_vm_paths.append(target_path)
                    updated_fields.append(field)
            elif mapping["file"] == "network":
                changed = False
                if net_data.get(target_path) != value:
                    net_updates[target_path] = value
                    changed_net_paths.append(target_path)
                    changed = True

                derive = mapping.get("derive")
                if derive and derive.get("from") == "bridgeEntity":
                    bridge_data = bridge_entities.get(value)
                    if bridge_data is None:
                        return _patch_error(
                            "INTERNAL_ERROR",
                            f"Bridge entity for '{value}' was not loaded", 500)
                    derived_value = bridge_data.get(derive.get("field"))
                    if derived_value is None:
                        return _patch_error(
                            "VALIDATION_ERROR",
                            f"Managed bridge '{value}' has no "
                            f"'{derive.get('field')}' value", 400)
                    if net_data.get(derive["path"]) != derived_value:
                        net_updates[derive["path"]] = derived_value
                        changed_net_paths.append(derive["path"])
                        changed = True

                if changed:
                    updated_fields.append(field)
            else:
                return _patch_error(
                    "INTERNAL_ERROR",
                    f"Unknown rest_map target file '{mapping['file']}'", 500)

        if not updated_fields:
            return jsonify({
                "success": True,
                "data": {
                    "name": name,
                    "updated": [],
                    "networkFile": os.path.basename(net_path),
                    "removedGeneratedFiles": [],
                    "vmState": vm_data.get("vmState"),
                    "warnings": [],
                }
            })

        # ── Write atomically, then re-validate the combined definition ──
        vm_file = vm_json_path(current_app, name)
        backups = {}
        try:
            if vm_updates:
                backups[vm_file] = _read_text(vm_file)
                new_vm_data = dict(vm_data)
                new_vm_data.update(vm_updates)
                _atomic_write_json(vm_file, new_vm_data)
            if net_updates:
                backups[net_path] = _read_text(net_path)
                new_net_data = dict(net_data)
                new_net_data.update(net_updates)
                _atomic_write_json(net_path, new_net_data)

            # KvmVm only validates here; it does not execute virsh commands.
            KvmVm(logger, vm_file, key_dir=_get_host_key_dir())
        except ValueError as e:
            _restore_files(backups, logger)
            return _patch_error("VALIDATION_ERROR", str(e), 400)
        except SystemError as e:
            _restore_files(backups, logger)
            return _patch_error("SYSTEM_COMMAND_FAILED", str(e), 500)
        except Exception as e:
            _restore_files(backups, logger)
            logger.error("Unexpected error while patching VM [%s]: %s", name, e)
            return _patch_error(
                "INTERNAL_ERROR", "Unexpected error while patching VM", 500)

        # ── Invalidate generated artifacts (data_map driven) ──
        changed_by_file = {}
        if changed_vm_paths:
            changed_by_file["vm"] = changed_vm_paths
        if changed_net_paths:
            changed_by_file["network"] = changed_net_paths

        ci_iso_path = _resolve_ci_iso_path(name, vm_data, vm_path)
        variables = {
            "vmPath": vm_path,
            "vmName": name,
            "ciIsoPath": ci_iso_path,
        }
        artifact_paths = artifact_paths_for_changes(
            changed_by_file, data_map, variables)
        removed, warnings = remove_artifacts(artifact_paths, vm_path)

        # A persistent (defined) domain would not be rebuilt by create_vm().
        if live_state == KvmVm.Keyword.VmStates.Stopped:
            try:
                Util.run_command(f"virsh undefine {name}")
            except SystemError as e:
                warnings.append(
                    f"Failed to undefine persistent domain '{name}': {e}")

        logger.info("PATCH VM [%s] updated fields: %s", name, updated_fields)
        return jsonify({
            "success": True,
            "data": {
                "name": name,
                "updated": updated_fields,
                "networkFile": os.path.basename(net_path),
                "removedGeneratedFiles": removed,
                "vmState": vm_data.get("vmState"),
                "warnings": warnings,
            }
        })


@vm_bp.route("/<name>", methods=["DELETE"])
def delete_vm(name: str):
    logger = _get_logger()
    with _get_vm_lock(name):
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
    with _get_vm_lock(name):
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
    with _get_vm_lock(name):
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


