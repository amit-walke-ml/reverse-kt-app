from app.core.config import settings
from app.core.security import hash_password, verify_password


def test_default_admin_password_roundtrip():
    hashed = hash_password(settings.default_admin_password)
    assert verify_password(settings.default_admin_password, hashed)
    assert not verify_password("wrong-password", hashed)
