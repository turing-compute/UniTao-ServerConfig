"""REST API schema — self-documenting endpoint metadata."""

from extlib.flask import Blueprint, jsonify

from rest.schema_loader import load_rest_schemas

schema_bp = Blueprint("schema", __name__)

_API_SCHEMA = {
    "service": "UniTao KVM Host",
    "version": "1.0",
    "basePath": "/api/v1",
    "schemaUrl": "/api/v1/schema",
    "resources": {
        "vms": {
            "description": "Virtual machine lifecycle management",
            "endpoints": {
                "GET /api/v1/vms": "List all VMs (managed + virsh-only)",
                "POST /api/v1/vms": "Create or update a VM. Body: id*, cpu*, ramInGB*, vmHostName*, osImage*, osVariant*, bridge*; optional: ipv4, gateway4, diskSizeGB, authType, customerPWD, customerKeys, shareInventoryData, prepareDomainImage",
                "GET /api/v1/vms/<name>": "Get VM details (virsh state, disk/net JSONs, inventory with timestamps). Unmanaged VMs return minimal info.",
                "PATCH /api/v1/vms/<name>": "Partially update a stopped VM. Allowed fields: cpu, ramInGB, bridge. See /api/v1/schema for the full PATCH schema.",
                "DELETE /api/v1/vms/<name>": "Delete VM (managed: destroy+remove dir; unmanaged: virsh destroy+undefine)",
                "POST /api/v1/vms/<name>/start": "Start VM (set vmState=running)",
                "POST /api/v1/vms/<name>/stop": "Stop VM (set vmState=stopped)",
                "POST /api/v1/vms/<name>/commit": "Commit qcow2 disk changes to backing image. Body: disk (int|string, optional). VM must be stopped+destroyed.",
                "GET /api/v1/vms/<name>/inventory": "List inventory file names",
                "GET /api/v1/vms/<name>/inventory/<f>": "Get inventory file with content and timestamp",
                "POST /api/v1/vms/<name>/inventory": "Post to inventory. Body.name → {name}.json (overwrite); no name → {timestamp}.json",
                "PATCH /api/v1/vms/<name>/inventory/<file>": "Patch an inventory file (JSON deep-merge). Only updates specified fields.",
            },
        },
        "images": {
            "description": "Disk image management",
            "endpoints": {
                "GET /api/v1/images": "List all images",
                "POST /api/v1/images/<name>": "Create/download image (async)",
                "GET /api/v1/images/<name>": "Get image details (downloadState)",
                "DELETE /api/v1/images/<name>": "Delete image and disk file",
            },
        },
        "bridges": {
            "description": "Network bridge management",
            "endpoints": {
                "GET /api/v1/bridges": "List bridges",
                "POST /api/v1/bridges/<name>": "Create/update bridge",
                "GET /api/v1/bridges/<name>": "Get bridge details",
                "DELETE /api/v1/bridges/<name>": "Delete bridge",
            },
        },
        "utils": {
            "endpoints": {
                "GET /api/v1/utils/health": "Health check",
                "GET /api/v1/utils/mac": "Generate random MAC address",
            },
        },
        "schema": {
            "endpoints": {
                "GET /api/v1/schema": "Aggregate all resource rest.json schemas",
            },
        },
    },
    "common": {
        "success": {"success": True, "data": "{...}"},
        "error": {"success": False, "error": {"code": "ERROR_CODE", "message": "..."}},
        "errorCodes": {
            "BAD_REQUEST": "400 — invalid input",
            "VALIDATION_ERROR": "400 — field validation failed",
            "EMPTY_PATCH": "400 — PATCH body has no fields",
            "INVALID_PATCH_FIELD": "400 — PATCH field is not in the allowed list",
            "NOT_FOUND": "404 — resource not found",
            "VM_RUNNING": "409 — VM must be stopped before PATCH",
            "VM_NOT_EXISTS": "409 — VM is marked notExists",
            "SYSTEM_COMMAND_FAILED": "500 — host command failed",
            "INTERNAL_ERROR": "500 — unexpected error",
            "NOT_IMPLEMENTED": "501 — operation not implemented (e.g. multi-network PATCH)",
        },
    },
}


@schema_bp.route("")
@schema_bp.route("/")
def schema_index():
    return jsonify({"success": True, "data": _API_SCHEMA})


@schema_bp.route("/schema")
def schema_all():
    """Return every resource schema (src/data/schema/*/rest.json)."""
    return jsonify({"success": True, "data": load_rest_schemas()})
