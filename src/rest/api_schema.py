"""REST API schema — self-documenting endpoint metadata.

Served at GET /api/v1/ to help API consumers understand all available
endpoints, their parameters, and response formats.
"""

from extlib.flask import Blueprint, jsonify

schema_bp = Blueprint("schema", __name__)

SCHEMA = {
    "service": "UniTao KVM Host",
    "version": "1.0",
    "basePath": "/api/v1",
    "resources": {
        "vms": {
            "description": "Virtual machine lifecycle management",
            "endpoints": {
                "list": {
                    "method": "GET",
                    "path": "/api/v1/vms",
                    "description": "List all VMs (managed + virsh-only)",
                    "response": {"vms": "[{name, virshState, managed}]"},
                },
                "create": {
                    "method": "POST",
                    "path": "/api/v1/vms",
                    "description": "Create or update a VM",
                    "body": {
                        "id": "string (required) — VM identifier",
                        "cpu": "int (required) — vCPU count",
                        "ramInGB": "int (required) — RAM in GB",
                        "vmHostName": "string (required) — hostname",
                        "osImage": "string (required) — managed image name",
                        "osVariant": "string (required) — e.g. ubuntu24.04",
                        "bridge": "string (required) — bridge name",
                        "ipv4": "string (optional) — static IP in CIDR, e.g. 192.168.1.10/24",
                        "gateway4": "string (optional) — gateway IP",
                        "diskSizeGB": "int (optional) — override disk size",
                        "authType": "string (optional) — CustomerPWD | RandomPWD | HostKey | CustomerKey | NoAuth",
                        "customerPWD": "string (required if authType=CustomerPWD)",
                        "customerKeys": "[string] (required if authType=CustomerKey)",
                        "shareInventoryData": "bool (default false) — inject inventory_tool.py",
                        "prepareDomainImage": "bool (default false) — inject prep_image_for_commit.py",
                    },
                    "response": {"name": "string", "vmPath": "string"},
                },
                "get": {
                    "method": "GET",
                    "path": "/api/v1/vms/<name>",
                    "description": "Get VM details including virsh state, disk/net JSONs, inventory",
                    "response": {"name": "string", "virshState": "string", "...": "data files"},
                },
                "delete": {
                    "method": "DELETE",
                    "path": "/api/v1/vms/<name>",
                    "description": "Delete a VM (destroy in virsh + remove data dir)",
                    "response": {"name": "string", "deleted": "true"},
                },
                "start": {
                    "method": "POST",
                    "path": "/api/v1/vms/<name>/start",
                    "description": "Start a VM (set vmState=running and process)",
                    "response": {"name": "string", "vmState": "running"},
                },
                "stop": {
                    "method": "POST",
                    "path": "/api/v1/vms/<name>/stop",
                    "description": "Stop a VM (set vmState=stopped and process)",
                    "response": {"name": "string", "vmState": "stopped"},
                },
                "patch": {
                    "method": "PATCH",
                    "path": "/api/v1/vms",
                    "description": "Patch VM state (start/stop by id)",
                    "body": {
                        "id": "string (required) — VM identifier",
                        "vmState": "string (required) — 'running' or 'stopped'",
                    },
                    "response": {"name": "string", "vmState": "string"},
                },
                "commit": {
                    "method": "POST",
                    "path": "/api/v1/vms/<name>/commit",
                    "description": "Commit VM disk changes to backing base image. VM must be stopped and destroyed.",
                    "body": {
                        "disk": "int|string (optional, default 0) — disk index or disk definition filename",
                    },
                    "response": {"vmName": "string", "imageName": "string", "diskPath": "string"},
                },
                "inventory-list": {
                    "method": "GET",
                    "path": "/api/v1/vms/<name>/inventory",
                    "description": "List inventory file names for a VM",
                    "response": {"name": "string", "files": "[string]"},
                },
                "inventory-get": {
                    "method": "GET",
                    "path": "/api/v1/vms/<name>/inventory/<filename>",
                    "description": "Get a specific inventory file with content and timestamp",
                    "response": {"name": "string", "file": "string", "content": "{...}", "timestamp": "ISO8601"},
                },
                "inventory-post": {
                    "method": "POST",
                    "path": "/api/v1/vms/<name>/inventory",
                    "description": "Post data to VM inventory. If body.name is set, stores as {name}.json (overwrite); otherwise {timestamp}.json.",
                    "body": {
                        "name": "string (optional) — filename without .json extension",
                        "...": "any JSON data",
                    },
                    "response": {"name": "string", "file": "string"},
                },
            },
        },
        "images": {
            "description": "Disk image management",
            "endpoints": {
                "list": {
                    "method": "GET", "path": "/api/v1/images",
                    "description": "List all images (managed + disk-only)",
                },
                "get": {
                    "method": "GET", "path": "/api/v1/images/<name>",
                    "description": "Get image details including downloadState",
                },
                "create": {
                    "method": "POST", "path": "/api/v1/images/<name>",
                    "description": "Create/download an image (async, poll downloadState)",
                },
                "delete": {
                    "method": "DELETE", "path": "/api/v1/images/<name>",
                    "description": "Delete image (JSON definition + disk file)",
                },
            },
        },
        "bridges": {
            "description": "Network bridge management",
            "endpoints": {
                "list": {
                    "method": "GET", "path": "/api/v1/bridges",
                    "description": "List all bridges",
                },
                "get": {
                    "method": "GET", "path": "/api/v1/bridges/<name>",
                    "description": "Get bridge details",
                },
                "create": {
                    "method": "POST", "path": "/api/v1/bridges/<name>",
                    "description": "Create/update bridge definition",
                },
                "delete": {
                    "method": "DELETE", "path": "/api/v1/bridges/<name>",
                    "description": "Delete bridge definition",
                },
            },
        },
        "utils": {
            "description": "Utility endpoints",
            "endpoints": {
                "health": {
                    "method": "GET", "path": "/api/v1/utils/health",
                    "description": "Health check",
                },
                "mac": {
                    "method": "GET", "path": "/api/v1/utils/mac",
                    "description": "Generate a random MAC address",
                },
            },
        },
        "wireguard": {
            "description": "WireGuard domain tool endpoints (planned)",
            "endpoints": {},
        },
    },
    "common": {
        "successResponse": {"success": "true", "data": "{...}"},
        "errorResponse": {"success": "false", "error": {"code": "ERROR_CODE", "message": "..."}},
        "errorCodes": [
            "BAD_REQUEST (400) — invalid input",
            "NOT_FOUND (404) — resource not found",
            "VALIDATION_ERROR (400) — field validation failed",
            "SYSTEM_COMMAND_FAILED (500) — host command failed",
            "INTERNAL_ERROR (500) — unexpected error",
        ],
    },
}


@schema_bp.route("", methods=["GET"])
def schema_index():
    return jsonify({
        "success": True,
        "data": SCHEMA,
    })
