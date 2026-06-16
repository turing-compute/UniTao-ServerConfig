"""Generate host RSA key pair for VM secure access.

Usage:
    ./src/runpy.sh src/security/generate_keys.py <key_dir>
"""

import sys

from src.security.key_manager import KeyManager


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <key_dir>")
        sys.exit(1)

    key_dir = sys.argv[1]
    km = KeyManager(key_dir)
    km.generate_keys()
    print(f"Key pair generated in {key_dir}")


if __name__ == "__main__":
    main()
