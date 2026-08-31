import pytest

from app.browser import _is_safe_url


@pytest.mark.parametrize(
    "url",
    [
        "http://1.1.1.1/",  # public IP literal — no real DNS lookup needed
        "https://1.1.1.1/status",
    ],
)
def test_accepts_public_looking_hosts(url):
    safe, reason = _is_safe_url(url)
    assert safe is True
    assert reason == ""


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://localhost:8080/api/ventures",
        "http://169.254.169.254/computeMetadata/v1/",  # GCP/AWS metadata endpoint
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://metadata.google.internal/",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "",
        "not a url",
    ],
)
def test_rejects_internal_and_non_http_urls(url):
    safe, reason = _is_safe_url(url)
    assert safe is False
    assert reason
