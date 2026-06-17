import secrets
import string

# Character set for generated passwords: letters, digits, and safe special characters
_PASSWORD_CHARS = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"


def generate_password(length: int = 20) -> str:
    """Generate a cryptographically secure random password."""
    return "".join(secrets.choice(_PASSWORD_CHARS) for _ in range(length))
