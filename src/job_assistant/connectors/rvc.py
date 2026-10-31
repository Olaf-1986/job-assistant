from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from ..rvc_store import RvcCard, RvcError

ENDPOINT = "https://app.rvc.global/mcp"
PROTOCOL = "2025-03-26"


class RvcSearchResult(BaseModel):
    status: Literal["results"]
    results: list[RvcCard] = Field(max_length=10)
    returned_count: int = Field(ge=0, le=10)
    total_available_count: int = Field(ge=0)
    truncated: bool

    @model_validator(mode="after")
    def check_counts(self) -> RvcSearchResult:
        if self.returned_count != len(self.results) or self.total_available_count < self.returned_count:
            raise ValueError("Inconsistent RVC result counts")
        if len({card.masked_id for card in self.results}) != len(self.results):
            raise ValueError("Duplicate RVC identities in response")
        if self.total_available_count > self.returned_count and not self.truncated:
            raise ValueError("RVC result omitted its truncation flag")
        return self


class RvcClient:
    """Bounded, read-only MCP discovery. No browser, catalog walk, or automatic retry."""

    def __init__(self, timeout: float = 30, transport: httpx.BaseTransport | None = None):
        self.timeout = timeout
        self.transport = transport

    def search(self, arguments: dict[str, Any]) -> RvcSearchResult:
        headers = {"Accept": "application/json, text/event-stream", "User-Agent": "job-assistant/rvc-pilot"}
        try:
            with httpx.Client(
                headers=headers, timeout=self.timeout, transport=self.transport, follow_redirects=False
            ) as client:
                initialized = self._rpc(
                    client,
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {},
                        "clientInfo": {"name": "job-assistant", "version": "0.1"},
                    },
                    1,
                )
                if initialized.get("protocolVersion") != PROTOCOL:
                    raise RvcError("Unsupported RVC MCP protocol; no search performed")
                client.headers["MCP-Protocol-Version"] = PROTOCOL
                self._rpc(client, "notifications/initialized", {}, None)
                result = self._rpc(client, "tools/call", {"name": "rvc_search_jobs", "arguments": arguments}, 2)
                if result.get("isError"):
                    raise RvcError("RVC search reported an error; response contents omitted")
                data = result.get("structuredContent")
                if isinstance(data, dict) and data.get("status") == "clarification_required":
                    raise RvcError("RVC requires a more specific role; retry explicitly with --query")
                return RvcSearchResult.model_validate(data)
        except httpx.HTTPStatusError as exc:
            raise RvcError(f"RVC HTTP {exc.response.status_code}; stopped without retry") from None
        except httpx.HTTPError:
            raise RvcError("RVC connection failed; stopped without retry") from None
        except (ValueError, ValidationError):
            raise RvcError("Unexpected RVC response schema; existing data retained") from None

    @staticmethod
    def _rpc(client: httpx.Client, method: str, params: dict[str, Any], request_id: int | None) -> dict:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        with client.stream("POST", ENDPOINT, json=payload) as response:
            response.raise_for_status()
            if method == "initialize" and "mcp-session-id" in response.headers:
                client.headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
            if request_id is None:
                return {}
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type == "application/json":
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_000_000:
                        raise RvcError("RVC response exceeded the pilot size limit")
                return _rpc_result(json.loads(body), request_id)
            if content_type != "text/event-stream":
                raise RvcError("Unexpected RVC content type; response contents omitted")
            lines: list[str] = []
            size = 0
            for line in response.iter_lines():
                size += len(line) + 1
                if size > 1_000_000:
                    raise RvcError("RVC response exceeded the pilot size limit")
                if line.startswith("data:"):
                    lines.append(line[5:].lstrip(" "))
                elif not line and lines:
                    message = json.loads("\n".join(lines))
                    lines = []
                    if isinstance(message, dict) and message.get("id") == request_id:
                        return _rpc_result(message, request_id)
            raise RvcError("RVC stream ended without a matching response")


def _rpc_result(message: Any, request_id: int) -> dict:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or message.get("id") != request_id:
        raise RvcError("Invalid RVC MCP response identity")
    if "error" in message or not isinstance(message.get("result"), dict):
        raise RvcError("RVC MCP request failed; response contents omitted")
    return message["result"]
