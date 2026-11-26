#!/usr/bin/env python3
"""Reconcile a deployed config.json with the repo's default config template.

Used by deploy-service.sh so that upgrading the code never leaves the runtime
config without a key the service needs (e.g. a newly added data directory).

- Config file absent  → start from the template as-is (no questions).
- Key in template but missing from config → ask for its value on the terminal,
  showing the template default; Enter accepts the default. Without a terminal
  (non-interactive deploy) the default is used and a warning is printed.
- Key in config but not in template → removed.
- --set KEY=VALUE     → always written (used for values the deploy script
  detects itself, such as hostKeyDir and hostApiUrl); never asked.

Values keep the template value's JSON type: a string default takes the raw
input, any other type (int, bool, ...) must be entered as JSON.
"""

import argparse
import json
import os
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Merge runtime config.json with the default template")
    parser.add_argument("--template", required=True, help="Default config template (src/rest/config.json)")
    parser.add_argument("--config", required=True, help="Runtime config file to create/update")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="Force a string value for KEY (repeatable)")
    return parser.parse_args()


def ask_value(key, default, interactive):
    if not interactive:
        print(f"WARNING: config key '{key}' is missing; no terminal to ask, using default {json.dumps(default)}")
        return default
    is_str = isinstance(default, str)
    while True:
        answer = input(f"Config key '{key}' is not set. Value [{json.dumps(default)}]: ").strip()
        if answer == "":
            return default
        if is_str:
            return answer
        try:
            value = json.loads(answer)
        except json.JSONDecodeError:
            print(f"  Invalid JSON, expected a {type(default).__name__} like {json.dumps(default)}")
            continue
        if type(value) is not type(default):
            print(f"  Expected a {type(default).__name__}, got {type(value).__name__}")
            continue
        return value


def main():
    args = parse_args()
    with open(args.template) as f:
        template = json.load(f)

    forced = {}
    for item in args.set:
        key, sep, value = item.partition("=")
        if not sep or not key:
            sys.exit(f"ERROR: --set expects KEY=VALUE, got '{item}'")
        forced[key] = value

    if os.path.exists(args.config):
        with open(args.config) as f:
            config = json.load(f)
        print(f"Config already exists at {args.config}")
        missing = [k for k in template if k not in config and k not in forced]
        if missing:
            print(f"Config is missing keys: {missing}")
            interactive = sys.stdin.isatty()
            for key in missing:
                config[key] = ask_value(key, template[key], interactive)
    else:
        print(f"Config not found, creating {args.config} from default template")
        config = dict(template)

    extra = [k for k in config if k not in template]
    for key in extra:
        del config[key]
    if extra:
        print(f"Removed unrecognized config keys: {extra}")

    for key, value in forced.items():
        config[key] = value
        print(f"Config {key} set to {value}")

    # Keep the template's key order so diffs against the template stay readable.
    ordered = {k: config[k] for k in template if k in config}
    os.makedirs(os.path.dirname(os.path.abspath(args.config)), exist_ok=True)
    tmp_path = args.config + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(ordered, f, indent=4)
        f.write("\n")
    os.replace(tmp_path, args.config)


if __name__ == "__main__":
    main()
