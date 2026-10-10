from __future__ import annotations

import asyncio
import base64
import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes
from jsonschema import Draft202012Validator

from app.agents import AgentDefinition
from app.identity import canonical_json
from app.providers import DeepSeekStructuredTaskProvider
from app.runtime_models import ManagedRuntime


def resolve_secret(reference: str) -> str:
    if reference.startswith("env:"):
        value = os.getenv(reference[4:], "")
    elif reference.startswith("file:"):
        value = Path(reference[5:]).read_text(encoding="utf-8").strip()
    else:
        raise ValueError("Credential references must use env: or file:")
    if not value:
        raise ValueError("Credential reference is empty")
    return value


def _public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_multicast or address.is_reserved or address.is_unspecified
    )


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, ip: str, port: int, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        peer = raw.getpeername()[0]
        if peer != self._pinned_ip or not _public_ip(peer):
            raw.close()
            raise ValueError("Connected peer IP failed SSRF policy")
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _allowed_http_target(url: str) -> tuple[str, int, str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Managed HTTP executor requires an HTTPS URL without userinfo or fragment")
    port = parsed.port or 443
    allowlist = {item.strip().lower() for item in os.getenv("MANAGED_HTTP_ALLOWLIST", "").split(",") if item.strip()}
    authority = f"{parsed.hostname.lower()}:{port}"
    if parsed.hostname.lower() not in allowlist and authority not in allowlist:
        raise ValueError("HTTP executor target is not in MANAGED_HTTP_ALLOWLIST")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return parsed.hostname, port, authority, path


def _https_json_request(url: str, payload: dict[str, object], timeout: float, max_bytes: int) -> dict[str, object]:
    hostname, port, authority, path = _allowed_http_target(url)
    addresses = {item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)}
    if not addresses or not all(_public_ip(address) for address in addresses):
        raise ValueError("HTTP executor DNS result contains a non-public address")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    last_error: Exception | None = None
    for address in sorted(addresses):
        connection = PinnedHTTPSConnection(hostname, address, port, timeout)
        try:
            connection.request("POST", path, body=body, headers={
                "Host": authority,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Length": str(len(body)),
            })
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ValueError("HTTP executor redirects are disabled")
            content = response.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise ValueError("HTTP executor response exceeds the configured size limit")
            if response.status < 200 or response.status >= 300:
                raise ValueError(f"HTTP executor returned status {response.status}")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("HTTP executor response must be a JSON object")
            return result
        except (OSError, ssl.SSLError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
        finally:
            connection.close()
    raise ValueError(f"HTTP executor failed: {last_error}")


@dataclass(frozen=True)
class ManagedIdentity:
    did: str
    public_key_hex: str

    @property
    def public_key_multibase(self) -> str:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), bytes.fromhex(self.public_key_hex.removeprefix("0x")))
        compressed = key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.CompressedPoint,
        )
        return "u" + base64.urlsafe_b64encode(compressed).rstrip(b"=").decode()

    def verify(self, payload: object, signature: str) -> None:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), bytes.fromhex(self.public_key_hex.removeprefix("0x")))
        raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        key.verify(raw, canonical_json(payload), ec.ECDSA(hashes.SHA256()))

    def verify_message(self, message: str, signature: str) -> None:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), bytes.fromhex(self.public_key_hex.removeprefix("0x")))
        raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        key.verify(raw, message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))


class SidecarClient:
    def __init__(self, runtime: ManagedRuntime):
        self.runtime = runtime
        self.token = resolve_secret(runtime.signer_credential_ref)
        parsed = urlparse(runtime.signer_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Managed signerUrl must use loopback HTTP")

    def request(self, path: str, payload: dict[str, object], worker_signature: str | None = None) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if worker_signature:
            headers["X-Worker-Intent-Signature"] = worker_signature
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.post(self.runtime.signer_url.rstrip("/") + path, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Signer returned a non-object response")
        return result

    def sign_identity_message(self, message: str) -> dict[str, object]:
        return self.request("/v1/sign/identity-message", {"runtimeId": self.runtime.runtime_id, "did": self.runtime.did, "message": message})

    def sign_runtime_result(self, payload: dict[str, object]) -> dict[str, object]:
        return self.request("/v1/sign/runtime-result", {"runtimeId": self.runtime.runtime_id, "did": self.runtime.did, "payload": payload})

    def sign_canonical_result(self, payload: dict[str, object]) -> dict[str, object]:
        return self.request("/v1/sign/canonical-result", payload)

    def sign_transaction(self, intent: dict[str, object], worker_signature: str) -> dict[str, object]:
        return self.request("/v1/sign/transaction", intent, worker_signature)

    def update_transaction_status(self, request_id: str, payload: dict[str, object], worker_signature: str, broadcast: bool = False) -> dict[str, object]:
        suffix = "broadcast-confirmation" if broadcast else "chain-status"
        return self.request(f"/v1/transactions/{request_id}/{suffix}", payload, worker_signature)


def runtime_capability(runtime: ManagedRuntime, capability_id: str | None = None) -> dict[str, object]:
    capabilities = [item for item in runtime.capabilities if isinstance(item, dict)]
    selected = next((item for item in capabilities if item.get("capabilityId") == capability_id), None) if capability_id else None
    selected = selected or (capabilities[0] if capabilities else None)
    if not selected:
        raise ValueError("Managed Runtime has no capability definition")
    return selected


async def execute_runtime(runtime: ManagedRuntime, payload: dict[str, object], capability_id: str | None = None) -> dict[str, object]:
    capability = runtime_capability(runtime, capability_id)
    input_schema = capability.get("inputSchema", {"type": "object"})
    output_schema = capability.get("outputSchema", {"type": "object"})
    Draft202012Validator(input_schema).validate(payload)
    config = runtime.executor_config
    if runtime.executor_type == "llm":
        provider = DeepSeekStructuredTaskProvider(
            api_key=resolve_secret(str(config.get("credentialRef", "env:DEEPSEEK_API_KEY"))),
            model=str(config.get("model", "deepseek-chat")),
            base_url=str(config.get("baseUrl", "https://api.deepseek.com")).rstrip("/"),
            timeout_seconds=min(float(config.get("timeoutSeconds", 60)), 120),
            max_retries=min(int(config.get("maxRetries", 2)), 3),
            temperature=0,
            max_tokens=min(int(config.get("maxTokens", 4096)), 8192),
        )
        if provider.base_url != "https://api.deepseek.com":
            raise ValueError("Managed DeepSeek executor baseUrl is fixed to https://api.deepseek.com")
        result = await provider.complete_json(
            system_prompt=str(config.get("systemPrompt", "Return a concise, auditable JSON result.")),
            task=payload,
            output_schema=output_schema,
        )
    elif runtime.executor_type == "http":
        result = await asyncio.to_thread(
            _https_json_request,
            str(config["url"]),
            payload,
            min(float(config.get("timeoutSeconds", 30)), 60),
            min(int(config.get("maxResponseBytes", 1048576)), 4194304),
        )
    else:
        raise ValueError("Unsupported managed executor type")
    Draft202012Validator(output_schema).validate(result)
    return result


def definition_from_runtime(runtime: ManagedRuntime) -> AgentDefinition:
    capability = runtime_capability(runtime)

    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return await execute_runtime(runtime, payload, str(capability["capabilityId"]))

    return AgentDefinition(
        slug=runtime.slug,
        name=runtime.name,
        description=runtime.description,
        capability_id=str(capability["capabilityId"]),
        version=runtime.version,
        base_price=str(capability.get("basePrice", "0")),
        currency=str(capability.get("currency", "CCIP-BNM")),
        estimated_duration_seconds=int(capability.get("estimatedDurationSeconds", 60)),
        input_schema=capability.get("inputSchema", {"type": "object"}),
        output_schema=capability.get("outputSchema", {"type": "object"}),
        example_input=capability.get("exampleInput", {}),
        handler=handler,
    )


def recover_and_validate_transaction(raw: str, intent: dict[str, object], expected_wallet: str, assigned_nonce: int) -> None:
    recovered = Account.recover_transaction(raw)
    if recovered.lower() != expected_wallet.lower():
        raise ValueError("Signer returned a transaction from the wrong wallet")
    actual = TypedTransaction.from_bytes(HexBytes(raw)).as_dict()
    expected = {
        "chainId": int(intent["chainId"]), "to": str(intent["to"]).lower(), "data": str(intent["data"]).lower(),
        "value": int(str(intent["value"])), "gas": int(intent["gasLimit"]), "nonce": assigned_nonce,
        "maxFeePerGas": int(str(intent["maxFeePerGas"])), "maxPriorityFeePerGas": int(str(intent["maxPriorityFeePerGas"])),
    }
    for key, value in expected.items():
        current = actual.get(key)
        if isinstance(current, bytes): current = "0x" + current.hex()
        if isinstance(value, str) and isinstance(current, str): current = current.lower()
        if current != value:
            raise ValueError(f"Signer changed transaction field {key}")
