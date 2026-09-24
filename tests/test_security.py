from cryptography.fernet import Fernet

from security import SecretBox, mask_secret, redact, secure_equals


def test_secret_box_roundtrip():
    box = SecretBox(Fernet.generate_key().decode())
    encrypted = box.encrypt("top-secret")
    assert encrypted != "top-secret"
    assert box.decrypt(encrypted) == "top-secret"


def test_mask_and_redaction():
    assert mask_secret("abcdefghij") == "abc…ij"
    text = (
        'Authorization: Bearer abc.def token=secret; "address":"wallet-value" '
        "123456789:abcdefghijklmnopqrstuvwxyz_ABC"
    )
    cleaned = redact(text)
    assert "abc.def" not in cleaned
    assert "secret" not in cleaned
    assert "wallet-value" not in cleaned
    assert "123456789" not in cleaned


def test_secure_equals_requires_nonempty_values():
    assert secure_equals("same", "same")
    assert not secure_equals("", "")
    assert not secure_equals(None, "secret")
