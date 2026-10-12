from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import socket
import statistics
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urljoin, urlparse

import httpx

from app.knowledge import ProjectKnowledgeIndex


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


class ControlledToolRegistry:
    """Allowlisted, read-only tools for project and public-network research."""

    def __init__(self, project_root: Path, knowledge_index: ProjectKnowledgeIndex) -> None:
        self.project_root = project_root.resolve()
        self.knowledge_index = knowledge_index
        self.platform_url = os.getenv(
            "AGENT_PLATFORM_QUERY_URL", os.getenv("AGENT_PLATFORM_URL", "http://127.0.0.1:8200")
        ).rstrip("/")
        self.platform_token = os.getenv("AGENT_PLATFORM_READ_TOKEN", "").strip()
        self.timeout_seconds = max(2.0, min(float(os.getenv("AGENT_TOOL_TIMEOUT_SECONDS", "20")), 60.0))
        self.rpc_urls = {
            int(os.getenv("LOCAL_CHAIN_A_ID", "31337")): os.getenv("LOCAL_CHAIN_A_RPC_URL", "http://127.0.0.1:8545"),
            int(os.getenv("LOCAL_CHAIN_B_ID", "31338")): os.getenv("LOCAL_CHAIN_B_RPC_URL", "http://127.0.0.1:8546"),
            int(os.getenv("LOCAL_CHAIN_C_ID", "31339")): os.getenv("LOCAL_CHAIN_C_RPC_URL", "http://127.0.0.1:8547"),
            int(os.getenv("LOCAL_CHAIN_D_ID", "31340")): os.getenv("LOCAL_CHAIN_D_RPC_URL", "http://127.0.0.1:8548"),
        }
        self._definitions: dict[str, tuple[str, dict[str, Any], ToolHandler]] = {
            "read_project_context": (
                "Search the platform documentation and return the most relevant cited sections.",
                _schema({"query": {"type": "string", "minLength": 1, "maxLength": 2000}}, ["query"]),
                self._read_project_context,
            ),
            "read_project_file": (
                "Read a bounded line range from an allowlisted project document or acceptance evidence JSON.",
                _schema(
                    {
                        "path": {"type": "string", "minLength": 1, "maxLength": 300},
                        "startLine": {"type": "integer", "minimum": 1, "maximum": 100000},
                        "endLine": {"type": "integer", "minimum": 1, "maximum": 100000},
                    },
                    ["path"],
                ),
                self._read_project_file,
            ),
            "get_workflow_status": (
                "Read one workflow and its node, attempt, settlement, and ledger status from the local platform API.",
                _schema({"workflowId": {"type": "string", "minLength": 3, "maxLength": 128}}, ["workflowId"]),
                self._get_workflow_status,
            ),
            "get_agent_status": (
                "Read an Agent profile by profile ID, Agent ID, Agent key, or slug from the local platform API.",
                _schema({"agent": {"type": "string", "minLength": 2, "maxLength": 128}}, ["agent"]),
                self._get_agent_status,
            ),
            "get_route_status": (
                "Read active and historical local cross-chain route status, optionally filtered by route or chain.",
                _schema(
                    {
                        "routeId": {"type": "string", "maxLength": 128},
                        "chainId": {"type": "integer", "minimum": 1},
                    }
                ),
                self._get_route_status,
            ),
            "get_wallet_balance": (
                "Read native or ERC-20 balance on one configured local EVM chain.",
                _schema(
                    {
                        "chainId": {"type": "integer", "minimum": 1},
                        "address": {"type": "string", "pattern": "^0x[a-fA-F0-9]{40}$"},
                        "tokenAddress": {"type": "string", "pattern": "^0x[a-fA-F0-9]{40}$"},
                        "decimals": {"type": "integer", "minimum": 0, "maximum": 36},
                    },
                    ["chainId", "address"],
                ),
                self._get_wallet_balance,
            ),
            "get_transaction_receipt": (
                "Read a transaction receipt from one configured local EVM chain.",
                _schema(
                    {
                        "chainId": {"type": "integer", "minimum": 1},
                        "transactionHash": {"type": "string", "pattern": "^0x[a-fA-F0-9]{64}$"},
                    },
                    ["chainId", "transactionHash"],
                ),
                self._get_transaction_receipt,
            ),
            "analyze_numbers": (
                "Calculate deterministic descriptive statistics for a bounded list of finite numbers.",
                _schema(
                    {
                        "numbers": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 1,
                            "maxItems": 1000,
                        }
                    },
                    ["numbers"],
                ),
                self._analyze_numbers,
            ),
            "web_search": (
                "Search public web sources using a fixed HTTPS search endpoint. Do not use for project implementation facts.",
                _schema({"query": {"type": "string", "minLength": 2, "maxLength": 500}}, ["query"]),
                self._web_search,
            ),
            "browse_url": (
                "Read bounded text from one public HTTPS URL. Private, loopback, link-local, and credential-bearing URLs are rejected.",
                _schema({"url": {"type": "string", "minLength": 8, "maxLength": 2000}}, ["url"]),
                self._browse_url,
            ),
        }

    def specifications(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {"name": name, "description": description, "parameters": schema},
            }
            for name, (description, schema, _) in self._definitions.items()
        ]

    def manifest(self) -> list[dict[str, object]]:
        return [
            {"name": name, "description": description, "readOnly": True, "audited": True}
            for name, (description, _, _) in self._definitions.items()
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.perf_counter()
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        audit: dict[str, Any] = {
            "name": name,
            "arguments": self._safe_arguments(arguments),
            "argumentsHash": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "ok": False,
        }
        definition = self._definitions.get(name)
        if definition is None:
            result = {"error": "Tool is not allowlisted"}
        else:
            try:
                result = await definition[2](arguments)
                audit["ok"] = True
            except (ValueError, OSError, httpx.HTTPError, KeyError, TypeError) as exc:
                result = {"error": str(exc)}
                audit["error"] = str(exc)[:500]
        audit["durationMs"] = round((time.perf_counter() - started) * 1000, 2)
        return result, audit

    @staticmethod
    def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in arguments.items():
            if any(marker in key.lower() for marker in ("key", "secret", "token", "password", "private")):
                safe[key] = "[REDACTED]"
            elif isinstance(value, str):
                safe[key] = value[:300]
            elif isinstance(value, list):
                safe[key] = value[:20]
            else:
                safe[key] = value
        return safe

    async def _read_project_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")
        return self.knowledge_index.search(query, max_sections=4, max_characters=12_000)

    async def _read_project_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        requested = str(arguments.get("path", "")).replace("\\", "/").lstrip("/")
        candidate = (self.project_root / requested).resolve()
        docs_root = (self.project_root / "docs").resolve()
        evidence_root = (self.project_root / ".runtime" / "local-experiment").resolve()
        evidence_names = {
            "protocol-worker-acceptance.json",
            "kimi-e2e-acceptance.json",
            "protocol-base.json",
            "chains.json",
        }
        allowed = candidate.is_relative_to(docs_root) or (
            candidate.parent == evidence_root and candidate.name in evidence_names
        )
        if not allowed or candidate.suffix.lower() not in {".md", ".json", ".txt"}:
            raise ValueError("path is outside the read-only documentation and evidence allowlist")
        if not candidate.is_file() or candidate.stat().st_size > 250_000:
            raise ValueError("file is missing or exceeds the 250 KB tool limit")
        lines = candidate.read_text(encoding="utf-8").splitlines()
        start = max(1, int(arguments.get("startLine", 1)))
        end = min(len(lines), int(arguments.get("endLine", start + 199)), start + 199)
        return {
            "path": str(candidate.relative_to(self.project_root)).replace("\\", "/"),
            "startLine": start,
            "endLine": end,
            "content": "\n".join(lines[start - 1 : end]),
        }

    def _platform_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.platform_token:
            headers["Authorization"] = f"Bearer {self.platform_token}"
        return headers

    async def _platform_get(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            response = await client.get(f"{self.platform_url}{path}", headers=self._platform_headers())
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("platform returned a non-object response")
        return payload

    async def _get_workflow_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        workflow_id = str(arguments.get("workflowId", "")).strip()
        if not workflow_id or not re.fullmatch(r"[A-Za-z0-9_-]{3,128}", workflow_id):
            raise ValueError("workflowId is invalid")
        return await self._platform_get(f"/api/v1/workflows/{workflow_id}")

    async def _get_agent_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        identifier = str(arguments.get("agent", "")).strip().lower()
        payload = await self._platform_get("/api/v1/workflow-agents")
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            agent = item.get("agent") if isinstance(item.get("agent"), dict) else {}
            candidates = (item.get("id"), item.get("agentId"), item.get("agentKey"), agent.get("id"), agent.get("slug"))
            if identifier in {str(value).lower() for value in candidates if value is not None}:
                return item
        raise ValueError("Agent was not found")

    async def _get_route_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        route_id = str(arguments.get("routeId", "")).strip().lower()
        chain_id = int(arguments["chainId"]) if arguments.get("chainId") is not None else None
        payload = await self._platform_get("/api/v1/chain-routes")
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        selected = [
            item
            for item in items
            if isinstance(item, dict)
            and (not route_id or str(item.get("id", "")).lower() == route_id or str(item.get("routeKey", "")).lower() == route_id)
            and (chain_id is None or int(item.get("destinationChainId", 0)) == chain_id)
        ]
        return {"items": selected[:50], "total": len(selected)}

    async def _rpc(self, chain_id: int, method: str, params: list[Any]) -> Any:
        rpc_url = self.rpc_urls.get(chain_id)
        if not rpc_url:
            raise ValueError("chainId is not in the configured local-chain allowlist")
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            response = await client.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            response.raise_for_status()
            payload = response.json()
        if payload.get("error"):
            raise ValueError(f"RPC error: {payload['error']}")
        return payload.get("result")

    async def _get_wallet_balance(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chain_id = int(arguments.get("chainId", 0))
        address = str(arguments.get("address", ""))
        if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
            raise ValueError("address is invalid")
        token = str(arguments.get("tokenAddress", ""))
        decimals = int(arguments.get("decimals", 18))
        if token:
            if not re.fullmatch(r"0x[a-fA-F0-9]{40}", token):
                raise ValueError("tokenAddress is invalid")
            data = "0x70a08231" + address[2:].lower().rjust(64, "0")
            raw = await self._rpc(chain_id, "eth_call", [{"to": token, "data": data}, "latest"])
            asset = token
        else:
            raw = await self._rpc(chain_id, "eth_getBalance", [address, "latest"])
            asset = "native"
        value = int(str(raw), 16)
        return {"chainId": chain_id, "address": address, "asset": asset, "raw": str(value), "formatted": str(value / (10**decimals)), "decimals": decimals}

    async def _get_transaction_receipt(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chain_id = int(arguments.get("chainId", 0))
        transaction_hash = str(arguments.get("transactionHash", ""))
        if not re.fullmatch(r"0x[a-fA-F0-9]{64}", transaction_hash):
            raise ValueError("transactionHash is invalid")
        receipt = await self._rpc(chain_id, "eth_getTransactionReceipt", [transaction_hash])
        return {"chainId": chain_id, "pending": receipt is None, "receipt": receipt}

    async def _analyze_numbers(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = arguments.get("numbers")
        if not isinstance(raw, list) or not raw or len(raw) > 1000:
            raise ValueError("numbers must contain between 1 and 1000 values")
        values = [float(item) for item in raw]
        if not all(math.isfinite(item) for item in values):
            raise ValueError("numbers must be finite")
        return {
            "count": len(values),
            "sum": math.fsum(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
            "populationStdev": statistics.pstdev(values),
        }

    async def _validate_public_https_url(self, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("URL must be public HTTPS without embedded credentials")
        records = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443)
        for record in records:
            address = ipaddress.ip_address(record[4][0])
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
                raise ValueError("URL resolves to a non-public address")
        return value

    async def _public_get(self, value: str) -> httpx.Response:
        current = await self._validate_public_https_url(value)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            for _ in range(4):
                response = await client.get(current, headers={"User-Agent": "CrossChainResearchAgent/1.0", "Accept": "text/html,application/json,text/plain"})
                if response.status_code not in {301, 302, 303, 307, 308}:
                    response.raise_for_status()
                    return response
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirect response omitted Location")
                current = await self._validate_public_https_url(urljoin(current, location))
        raise ValueError("too many redirects")

    @staticmethod
    def _response_text(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "").lower()
        if not any(item in content_type for item in ("text/", "json", "xml", "html")):
            raise ValueError("URL did not return text, HTML, XML, or JSON")
        if len(response.content) > 250_000:
            raise ValueError("URL response exceeds the 250 KB tool limit")
        text = response.text
        if "html" in content_type:
            text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()[:20_000]

    async def _browse_url(self, arguments: dict[str, Any]) -> dict[str, Any]:
        response = await self._public_get(str(arguments.get("url", "")))
        return {"url": str(response.url), "contentType": response.headers.get("content-type", ""), "content": self._response_text(response)}

    async def _web_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")
        url = "https://api.duckduckgo.com/?" + urlencode({"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"})
        response = await self._public_get(url)
        payload = response.json()
        results: list[dict[str, str]] = []
        if payload.get("AbstractText"):
            results.append({"title": str(payload.get("Heading") or query), "url": str(payload.get("AbstractURL") or ""), "snippet": str(payload["AbstractText"])})
        for item in payload.get("RelatedTopics") or []:
            candidates = item.get("Topics") if isinstance(item, dict) and isinstance(item.get("Topics"), list) else [item]
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("Text"):
                    results.append({"title": str(candidate.get("Text"))[:160], "url": str(candidate.get("FirstURL") or ""), "snippet": str(candidate.get("Text"))})
                if len(results) >= 8:
                    break
            if len(results) >= 8:
                break
        return {"query": query, "results": results, "note": "Search results are external evidence and must be independently verified."}
