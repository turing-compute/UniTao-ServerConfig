#!/usr/bin/env python3

import json
import logging
import os

from extlib.flask import Flask, jsonify

from security.key_manager import KeyManager
from shared.logger import Log
from rest.api_vm import vm_bp
from rest.api_image import image_bp, recover_stale_images
from rest.api_bridge import bridge_bp
from rest.api_schema import schema_bp
from rest.api_utils import utils_bp

# Config keys that are directory paths and should be resolved to absolute paths.
_DIR_KEYS = ["vmDataDir", "imageDataDir", "imageFileDir", "bridgeDataDir", "hostKeyDir"]


def load_config(config_path: str = None) -> dict:
    """Load configuration from a JSON file."""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r") as f:
        config = json.load(f)

    # Resolve relative directory paths against config file directory.
    config_dir = os.path.dirname(os.path.abspath(config_path))
    for key in _DIR_KEYS:
        value = config.get(key, None)
        if value is not None and not os.path.isabs(value):
            config[key] = os.path.abspath(os.path.join(config_dir, value))

    return config


def _ensure_data_dirs(config: dict):
    """Create data directories from config if they don't exist."""
    for key in _DIR_KEYS:
        d = config.get(key)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)


def _recover_vms_after_reboot(app, logger):
    """After host reboot, restart VMs whose desired state is 'running'."""
    from rest.service import list_entities, vm_json_path
    from kvm.vm.kvm_vm import KvmVm

    host_key_dir = app.config["CONFIG"].get("hostKeyDir", "/opt/unitiao/keys")
    for vm_name in list_entities(app, "vm"):
        try:
            vm_path = vm_json_path(app, vm_name)
            with open(vm_path, "r") as f:
                import json as _json
                data = _json.load(f)
        except Exception:
            continue
        if data.get("vmState") != "running":
            continue
        logger.warning("Recovering VM [%s] after host reboot", vm_name)
        try:
            vm = KvmVm(logger, vm_path, key_dir=host_key_dir)
            vm.Process()
        except Exception as e:
            logger.error("Failed to recover VM [%s]: %s", vm_name, e)


def create_app(config: dict = None) -> Flask:
    if config is None:
        config = load_config()

    _ensure_data_dirs(config)

    # Initialize KeyManager; warn but don't block startup if keys are missing.
    logger = logging.getLogger(__name__)
    key_manager = KeyManager(config["hostKeyDir"])
    if not key_manager.keys_exist():
        logger.warning("Host key pair not found in %s — "
                       "encrypted VM passwords will not be available. "
                       "Run deploy-service.sh or generate_keys.py to create the key pair.",
                       config["hostKeyDir"])
    else:
        key_manager.load_keys()
        logger.info("Host key pair loaded from %s", config["hostKeyDir"])

    app = Flask(__name__)
    app.config["CONFIG"] = config
    app.config["KEY_MANAGER"] = key_manager
    recover_stale_images(app)
    _recover_vms_after_reboot(app, logger)
    app.config["CONFIG"] = config
    app.config["JSONIFY_PRETTYPRINT_REGULAR"] = True

    @app.route("/")
    def root():
        endpoints = {}
        for rule in app.url_map.iter_rules():
            if rule.endpoint == "root":
                continue
            methods = sorted([m for m in rule.methods if m not in ("HEAD", "OPTIONS")])
            endpoints[rule.endpoint] = f"{'|'.join(methods)} {rule.rule}"
        return jsonify({
            "success": True,
            "data": {
                "service": "UniTao KVM Host",
                "endpoints": endpoints
            }
        })

    app.register_blueprint(schema_bp, url_prefix="/api/v1")
    app.register_blueprint(vm_bp, url_prefix="/api/v1/vms")
    app.register_blueprint(image_bp, url_prefix="/api/v1/images")
    app.register_blueprint(bridge_bp, url_prefix="/api/v1/bridges")
    app.register_blueprint(utils_bp, url_prefix="/api/v1/utils")

    register_error_handlers(app)

    return app


def register_error_handlers(app: Flask):
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({
            "success": False,
            "error": {"code": "BAD_REQUEST", "message": str(e.description)}
        }), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({
            "success": False,
            "error": {"code": "NOT_FOUND", "message": str(e.description) if e.description else "Resource not found"}
        }), 404

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({
            "success": False,
            "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}
        }), 500

    @app.errorhandler(SystemError)
    def system_error(e):
        return jsonify({
            "success": False,
            "error": {"code": "SYSTEM_COMMAND_FAILED", "message": str(e)}
        }), 500

    @app.errorhandler(ValueError)
    def value_error(e):
        return jsonify({
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": str(e)}
        }), 400


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="UniTao REST Agent")
    parser.add_argument("--config", type=str,
                        default="/etc/unitiao/config.json",
                        help="Path to config.json")
    args = parser.parse_args()

    logger = Log.get_logger("REST")
    config = load_config(args.config)
    host = config.get("host", "0.0.0.0")
    port = config.get("port", 5000)
    logger.info(f"Starting UniTao REST Agent on {host}:{port}...")
    app = create_app(config)
    app.run(host=host, port=port, debug=False)
