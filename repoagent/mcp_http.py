"""Origin-scoped HTTP transport with validated, pinned network destinations."""

import ipaddress
import socket
from urllib.parse import urlsplit

from .security import NetworkPolicy, NetworkPolicyError


class MCPHTTPPolicy:
    def __init__(self, url, *, allow_private=False, network_policy=None):
        self.url = url
        self.allow_private = allow_private
        self.network_policy = network_policy
        self.origin = self._origin(url)
        self.validate_url(url)

    @staticmethod
    def _origin(url):
        parsed = urlsplit(str(url))
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise NetworkPolicyError("invalid MCP endpoint port") from exc
        return parsed.scheme, (parsed.hostname or "").lower(), port

    def validate_url(self, url):
        NetworkPolicy(allow_private=self.allow_private).validate_url(str(url))
        parsed = urlsplit(str(url))
        if parsed.fragment or "%" in (parsed.hostname or ""):
            raise NetworkPolicyError(
                "MCP endpoints cannot contain fragments or scoped hosts"
            )
        if self._origin(url) != self.origin:
            raise NetworkPolicyError(
                "MCP requests must remain on the configured origin"
            )
        if self.network_policy is not None:
            self.network_policy.validate_url(str(url))

    def validate_address(self, value):
        address = ipaddress.ip_address(value)
        address = getattr(address, "ipv4_mapped", None) or address
        if (
            address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        ):
            raise NetworkPolicyError("special MCP network destinations are denied")
        if not self.allow_private and not address.is_global:
            raise NetworkPolicyError("private MCP network destinations are denied")
        if (
            self.network_policy is not None
            and not self.network_policy.allow_private
            and not address.is_global
        ):
            raise NetworkPolicyError("runtime policy denies private MCP destinations")

    async def resolve(self, url):
        self.validate_url(url)
        _, host, port = self.origin
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            import anyio

            records = await anyio.to_thread.run_sync(
                lambda: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM),
                abandon_on_cancel=True,
            )
            addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses:
            raise NetworkPolicyError("MCP endpoint has no network addresses")
        for address in addresses:
            self.validate_address(address)
        return addresses[0]


def build_http_client(config, policy, *, headers=None, timeout=None, auth=None):
    import httpx

    if auth is not None:
        raise NetworkPolicyError("automatic MCP authentication flows are not enabled")

    class PinnedTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.AsyncHTTPTransport(trust_env=False, retries=0)

        async def handle_async_request(self, request):
            address = await policy.resolve(str(request.url))
            extensions = dict(request.extensions)
            extensions["sni_hostname"] = request.url.host
            pinned = httpx.Request(
                request.method,
                request.url.copy_with(host=address),
                headers=request.headers,
                stream=request.stream,
                extensions=extensions,
            )
            response = await self.inner.handle_async_request(pinned)
            if 300 <= response.status_code < 400:
                await response.aclose()
                raise NetworkPolicyError("MCP redirects are disabled")
            return response

        async def aclose(self):
            await self.inner.aclose()

    return httpx.AsyncClient(
        headers={**config.headers, **(headers or {})},
        timeout=timeout
        or httpx.Timeout(config.startup_timeout, read=config.tool_timeout),
        transport=PinnedTransport(),
        follow_redirects=False,
        trust_env=False,
    )
