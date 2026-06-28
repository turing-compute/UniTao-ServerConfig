"""REST API schema — self-documenting endpoint metadata."""

from extlib.flask import Blueprint, jsonify

schema_bp = Blueprint("schema", __name__)

_API_SCHEMA = {
    "service": "UniTao KVM Host",
    "version": "1.0",
    "basePath": "/api/v1",
    "resources": {
        "vms": {
            "description": "Virtual machine lifecycle management",
            "endpoints": {
                "GET /api/v1/vms": "List all VMs (managed + virsh-only)",
                "POST /api/v1/vms": "Create or update a VM. Body: id*, cpu*, ramInGB*, vmHostName*, osImage*, osVariant*, bridge*; optional: ipv4, gateway4, diskSizeGB, authType, customerPWD, customerKeys, shareInventoryData, prepareDomainImage",
                "GET /api/v1/vms/<name>": "Get VM details (virsh state, disk/net JSONs, inventory with timestamps). Unmanaged VMs return minimal info.",
                "DELETE /api/v1/vms/<name>": "Delete VM (managed: destroy+remove dir; unmanaged: virsh destroy+undefine)",
                "POST /api/v1/vms/<name>/start": "Start VM (set vmState=running)",
                "POST /api/v1/vms/<name>/stop": "Stop VM (set vmState=stopped)",
                "POST /api/v1/vms/<name>/commit": "Commit qcow2 disk changes to backing image. Body: disk (int|string, optional). VM must be stopped+destroyed.",
                "GET /api/v1/vms/<name>/inventory": "List inventory file names",
                "GET /api/v1/vms/<name>/inventory/<f>": "Get inventory file with content and timestamp",
                "POST /api/v1/vms/<name>/inventory": "Post to inventory. Body.name → {name}.json (overwrite); no name → {timestamp}.json",
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
    },
    "common": {
        "success": {"success": True, "data": "{...}"},
        "error": {"success": False, "error": {"code": "ERROR_CODE", "message": "..."}},
        "errorCodes": {
            "BAD_REQUEST": "400 — invalid input",
            "VALIDATION_ERROR": "400 — field validation failed",
            "NOT_FOUND": "404 — resource not found",
            "SYSTEM_COMMAND_FAILED": "500 — host command failed",
            "INTERNAL_ERROR": "500 — unexpected error",
        },
    },
}


@schema_bp.route("")
@schema_bp.route("/")
def schema_index():
    return jsonify({"success": True, "data": _API_SCHEMA})
