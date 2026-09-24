import pytest

from moneyx.domain import DomainValidationError, derive_api_url, parse_web_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mxc1n.com", "https://mxc1n.com"),
        ("https://MXC1N.com/", "https://mxc1n.com"),
        ("https://sub.example.com", "https://sub.example.com"),
    ],
)
def test_parse_web_url(raw, expected):
    assert parse_web_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://user:pass@example.com",
        "https://example.com:8443",
        "https://example.com/path",
        "https://example.com?q=1",
    ],
)
def test_rejects_unsafe_domain(raw):
    with pytest.raises(DomainValidationError):
        parse_web_url(raw)


def test_derive_api_domain():
    assert derive_api_url("https://mxc1n.com") == "https://api.mxc1n.com"
