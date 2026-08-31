from __future__ import annotations
import http.client
import ipaddress
import socket
import ssl
import urllib.parse

_ALLOWED_SCHEMES = {"http", "https"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validated_target(url: str) -> tuple[str, urllib.parse.SplitResult, list[str]]:
    """Resolve one HTTP(S) URL and return only public destination addresses."""
    # Note: Resolution is retained for the actual socket connection so DNS rebinding cannot replace a validated public address with a private one afterward.
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        addresses = []
        for info in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM):
            address = str(info[4][0] or "")
            if address and address not in addresses:
                addresses.append(address)
    except (socket.gaierror, UnicodeError) as exc:
        raise ValueError("URL host could not be resolved") from exc
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise PermissionError("Local or private network URLs are not allowed")
    clean = urllib.parse.urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    return clean, urllib.parse.urlsplit(clean), addresses


def validate_public_url(url: str) -> str:
    """Validate an outbound HTTP(S) URL and reject local/private destinations."""
    # Note: Every fetch and redirect is revalidated to prevent RSS/favicon SSRF through private network targets.
    clean, _parsed, _addresses = _validated_target(url)
    return clean


def _connect_validated(parsed: urllib.parse.SplitResult, addresses: list[str], timeout: float) -> http.client.HTTPConnection:
    """Open HTTP(S) using one already-validated numeric destination while preserving Host/SNI validation."""
    # Note: HTTPS keeps normal CA and hostname verification; there is deliberately no insecure TLS fallback.
    host = str(parsed.hostname or "")
    scheme = parsed.scheme.lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    last_error: Exception | None = None
    for address in addresses:
        sock = None
        try:
            sock = socket.create_connection((address, port), timeout=timeout)
            if scheme == "https":
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=host)
                connection: http.client.HTTPConnection = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
            else:
                connection = http.client.HTTPConnection(host, port, timeout=timeout)
            connection.sock = sock
            return connection
        except Exception as exc:
            last_error = exc
            try:
                if sock:
                    sock.close()
            except Exception:
                pass
    if last_error:
        raise last_error
    raise OSError("No validated destination address is available")


def fetch_public(url: str, *, headers: dict[str, str] | None = None, timeout: float = 12, limit: int = 2_000_000, max_redirects: int = 5) -> tuple[bytes, str, str]:
    """Fetch bounded public HTTP(S) content while validating every redirect target."""
    # Note: Each request connects directly to the public IP resolved during validation, closing redirect and DNS-rebinding SSRF gaps.
    current = str(url or "")
    body_limit = max(0, int(limit))
    for _ in range(max(0, int(max_redirects)) + 1):
        current, parsed, addresses = _validated_target(current)
        connection = _connect_validated(parsed, addresses, float(timeout))
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            connection.request("GET", path, headers=dict(headers or {}))
            response = connection.getresponse()
            if response.status in _REDIRECT_STATUSES:
                location = response.getheader("Location")
                response.read(min(body_limit, 8192))
                if not location:
                    raise ValueError("Redirect response is missing Location")
                current = urllib.parse.urljoin(current, location)
                continue
            if response.status < 200 or response.status >= 300:
                raise OSError(f"HTTP request failed with status {response.status}")
            data = response.read(body_limit + 1)
            if len(data) > body_limit:
                raise ValueError("HTTP response exceeds the configured size limit")
            content_type = str(response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            return data, content_type, current
        finally:
            connection.close()
    raise ValueError("Too many redirects")
