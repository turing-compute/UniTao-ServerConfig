import base64
import logging
import os
import stat

from extlib.cryptography.hazmat.backends import default_backend
from extlib.cryptography.hazmat.primitives import hashes, serialization
from extlib.cryptography.hazmat.primitives.asymmetric import padding, rsa


class KeyManager:
    """RSA key management, encryption, and decryption for VM secure access."""

    ENCRYPTED_PREFIX = "ENC:"

    _PRIVATE_KEY_FILE = "host_private_key.pem"
    _PUBLIC_KEY_FILE = "host_public_key.pem"

    def __init__(self, key_dir: str):
        self._key_dir = key_dir
        self._private_key = None
        self._public_key = None
        self._loaded = False

        # Auto-create key directory
        os.makedirs(key_dir, exist_ok=True)

    @property
    def key_dir(self) -> str:
        return self._key_dir

    def _private_key_path(self) -> str:
        return os.path.join(self._key_dir, self._PRIVATE_KEY_FILE)

    def _public_key_path(self) -> str:
        return os.path.join(self._key_dir, self._PUBLIC_KEY_FILE)

    def keys_exist(self) -> bool:
        return os.path.isfile(self._private_key_path()) and os.path.isfile(self._public_key_path())

    def generate_keys(self, key_size: int = 4096):
        """Generate RSA key pair and save as PEM files. Private key permissions set to 600."""
        logger = logging.getLogger(__name__)

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend(),
        )

        # Serialize and write private key
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        priv_path = self._private_key_path()
        with open(priv_path, "wb") as f:
            f.write(private_pem)
        os.chmod(priv_path, stat.S_IRUSR | stat.S_IWUSR)

        # Serialize and write public key
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        pub_path = self._public_key_path()
        with open(pub_path, "wb") as f:
            f.write(public_pem)

        # Keep keys in memory
        self._private_key = private_key
        self._public_key = public_key
        self._loaded = True

        logger.info("RSA %d key pair generated in %s", key_size, self._key_dir)

    def load_keys(self):
        """Load existing key pair from PEM files into memory."""
        logger = logging.getLogger(__name__)

        if not self.keys_exist():
            raise FileNotFoundError(f"Key files not found in {self._key_dir}")

        with open(self._private_key_path(), "rb") as f:
            self._private_key = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )

        with open(self._public_key_path(), "rb") as f:
            self._public_key = serialization.load_pem_public_key(
                f.read(), backend=default_backend()
            )

        self._loaded = True
        logger.debug("Keys loaded from %s", self._key_dir)

    def is_loaded(self) -> bool:
        return self._loaded

    def _ensure_loaded(self):
        if not self._loaded:
            self.load_keys()

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext with RSA-OAEP-SHA256. Returns ENC: + base64 ciphertext."""
        self._ensure_loaded()

        ciphertext = self._public_key.encrypt(
            plaintext.encode("utf-8"),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return self.ENCRYPTED_PREFIX + base64.b64encode(ciphertext).decode("ascii")

    def decrypt(self, ciphertext_b64: str) -> str:
        """Decrypt a base64-encoded ciphertext (without the ENC: prefix)."""
        self._ensure_loaded()

        ciphertext = base64.b64decode(ciphertext_b64)
        plaintext = self._private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return plaintext.decode("utf-8")

    def decrypt_if_encrypted(self, value: str) -> str:
        """If value has ENC: prefix, decrypt and return plaintext; otherwise return as-is."""
        if self.is_encrypted_value(value):
            return self.decrypt(value[len(self.ENCRYPTED_PREFIX):])
        return value

    @staticmethod
    def is_encrypted_value(value: str) -> bool:
        return isinstance(value, str) and value.startswith(KeyManager.ENCRYPTED_PREFIX)

    def get_public_key_openssh(self) -> str:
        """Return the host public key in OpenSSH format (for cloud-init ssh_authorized_keys)."""
        self._ensure_loaded()

        return (
            self._public_key.public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            )
            .decode("ascii")
        )
