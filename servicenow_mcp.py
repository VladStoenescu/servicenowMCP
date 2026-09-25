#!/usr/bin/env python3
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = {"2024-10-07", PROTOCOL_VERSION}
JSONRPC_VERSION = "2.0"


class ServiceNowError(Exception):
    pass


@dataclass(frozen=True)
class ServiceNowConfig:
    base_url: str
    username: str | None = None
    password: str | None = None
    token: str | None = None

    @classmethod
    def from_env(cls) -> "ServiceNowConfig":
        raw_instance = os.getenv("SERVICENOW_INSTANCE_URL") or os.getenv("SERVICENOW_INSTANCE")
        if not raw_instance:
            raise ServiceNowError(
                "Set SERVICENOW_INSTANCE or SERVICENOW_INSTANCE_URL before starting the MCP server."
            )

        token = os.getenv("SERVICENOW_TOKEN")
        username = os.getenv("SERVICENOW_USERNAME")
        password = os.getenv("SERVICENOW_PASSWORD")
        if not token and not (username and password):
            raise ServiceNowError(
                "Authenticate with either SERVICENOW_TOKEN or SERVICENOW_USERNAME and SERVICENOW_PASSWORD."
            )

        return cls(
            base_url=normalize_instance_url(raw_instance),
            username=username,
            password=password,
            token=token,
        )


def normalize_instance_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ServiceNowError("ServiceNow instance value cannot be empty.")
    if not normalized.startswith(("http://", "https://")):
        normalized = normalized if "." in normalized else f"{normalized}.service-now.com"
        normalized = f"https://{normalized}"
    return normalized.rstrip("/")


class ServiceNowClient:
    def __init__(self, config: ServiceNowConfig) -> None:
        self._config = config

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._config.token:
            headers["Authorization"] = "Bearer " + self._config.token
        else:
            credentials = f"{self._config.username}:{self._config.password}".encode("utf-8")
            headers["Authorization"] = f"Basic {base64.b64encode(credentials).decode('ascii')}"
        return headers

    def request_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None}, doseq=False)
        url = f"{self._config.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise ServiceNowError(f"ServiceNow request failed ({error.code}): {body}") from error
        except urllib.error.URLError as error:
            raise ServiceNowError(f"Could not reach ServiceNow: {error.reason}") from error

    def query_records(
        self,
        table: str,
        query: str = "",
        fields: list[str] | None = None,
        limit: int = 10,
        offset: int = 0,
        display_value: bool = True,
    ) -> dict[str, Any]:
        response = self.request_json(
            f"/api/now/table/{urllib.parse.quote(table, safe='')}",
            {
                "sysparm_query": query or None,
                "sysparm_fields": ",".join(fields) if fields else None,
                "sysparm_limit": limit,
                "sysparm_offset": offset,
                "sysparm_display_value": str(display_value).lower(),
                "sysparm_exclude_reference_link": "true",
            },
        )
        return {
            "table": table,
            "query": query,
            "fields": fields or [],
            "limit": limit,
            "offset": offset,
            "result": response.get("result", []),
        }

    def get_record(self, table: str, sys_id: str, fields: list[str] | None = None) -> dict[str, Any]:
        response = self.request_json(
            f"/api/now/table/{urllib.parse.quote(table, safe='')}/{urllib.parse.quote(sys_id, safe='')}",
            {
                "sysparm_fields": ",".join(fields) if fields else None,
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            },
        )
        return {
            "table": table,
            "sys_id": sys_id,
            "fields": fields or [],
            "result": response.get("result", {}),
        }

    def _get_all_results(self, path: str, params: dict[str, Any], page_size: int = 100) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = self.request_json(
                path,
                {
                    **params,
                    "sysparm_limit": page_size,
                    "sysparm_offset": offset,
                },
            )
            page = response.get("result", [])
            if not isinstance(page, list):
                raise ServiceNowError("Expected a list result from ServiceNow.")
            results.extend(page)
            if len(page) < page_size:
                return results
            offset += len(page)

    def describe_table(self, table: str) -> dict[str, Any]:
        table_info = self.request_json(
            "/api/now/table/sys_db_object",
            {
                "sysparm_query": f"name={table}",
                "sysparm_fields": "name,label,super_class,provider_class,sys_scope",
                "sysparm_limit": 1,
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            },
        ).get("result", [])

        columns = self._get_all_results(
            "/api/now/table/sys_dictionary",
            {
                "sysparm_query": f"name={table}^elementISNOTEMPTY",
                "sysparm_fields": "element,column_label,internal_type,mandatory,max_length,reference",
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            },
        )

        return {
            "table": table,
            "details": table_info[0] if table_info else {},
            "columns": columns,
        }


class ServiceNowMCPServer:
    def __init__(self, client: ServiceNowClient) -> None:
        self._client = client

    def list_tools(self) -> list[dict[str, Any]]:
        shared_table_field = {"type": "string", "description": "ServiceNow table name, for example incident."}
        shared_fields = {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of fields to return.",
        }
        return [
            {
                "name": "query_records",
                "description": "Query ServiceNow table records with an encoded query.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "table": shared_table_field,
                        "query": {
                            "type": "string",
                            "description": "Optional ServiceNow encoded query, for example active=true^priority=1.",
                        },
                        "fields": shared_fields,
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "description": "Maximum number of records to return.",
                            "default": 10,
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Record offset for pagination.",
                            "default": 0,
                        },
                    },
                    "required": ["table"],
                },
            },
            {
                "name": "get_record",
                "description": "Fetch a single ServiceNow record by sys_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "table": shared_table_field,
                        "sys_id": {"type": "string", "description": "The sys_id of the record to fetch."},
                        "fields": shared_fields,
                    },
                    "required": ["table", "sys_id"],
                },
            },
            {
                "name": "describe_table",
                "description": "Inspect basic metadata and columns for a ServiceNow table.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "table": shared_table_field,
                    },
                    "required": ["table"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "query_records":
            payload = self._client.query_records(
                table=required_string(arguments, "table"),
                query=str(arguments.get("query", "") or ""),
                fields=optional_string_list(arguments.get("fields")),
                limit=bounded_int(arguments.get("limit", 10), minimum=1, maximum=100, field_name="limit"),
                offset=bounded_int(arguments.get("offset", 0), minimum=0, maximum=100000, field_name="offset"),
            )
        elif name == "get_record":
            payload = self._client.get_record(
                table=required_string(arguments, "table"),
                sys_id=required_string(arguments, "sys_id"),
                fields=optional_string_list(arguments.get("fields")),
            )
        elif name == "describe_table":
            payload = self._client.describe_table(required_string(arguments, "table"))
        else:
            raise ServiceNowError(f"Unknown tool: {name}")

        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}

    def process_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        has_request_id = "id" in request
        request_id = request.get("id")

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            params = request.get("params") or {}
            client_version = params.get("protocolVersion")
            if client_version not in SUPPORTED_PROTOCOL_VERSIONS:
                return error_response(
                    request_id,
                    -32602,
                    f"Unsupported protocolVersion: {client_version}",
                )
            return success_response(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "servicenow-local-mcp",
                        "version": "0.1.0",
                    },
                },
            )

        if method == "ping":
            if not has_request_id:
                return None
            return success_response(request_id, {})

        if method == "tools/list":
            if not has_request_id:
                return None
            return success_response(request_id, {"tools": self.list_tools()})

        if method == "tools/call":
            if not has_request_id:
                return None
            params = request.get("params") or {}
            try:
                result = self.call_tool(str(params.get("name", "")), params.get("arguments") or {})
            except ServiceNowError as error:
                return success_response(
                    request_id,
                    {"content": [{"type": "text", "text": str(error)}], "isError": True},
                )
            return success_response(request_id, result)

        if not has_request_id:
            return None

        return error_response(request_id, -32601, f"Method not found: {method}")


def required_string(arguments: dict[str, Any], field_name: str) -> str:
    value = arguments.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ServiceNowError(f"{field_name} must be a non-empty string.")
    return value.strip()


def optional_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ServiceNowError("fields must be an array of non-empty strings.")
    return [item.strip() for item in value]


def bounded_int(value: Any, minimum: int, maximum: int, field_name: str) -> int:
    if isinstance(value, bool):
        raise ServiceNowError(f"{field_name} must be an integer.")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ServiceNowError(f"{field_name} must be an integer.") from error
    if number < minimum or number > maximum:
        raise ServiceNowError(f"{field_name} must be between {minimum} and {maximum}.")
    return number


def success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def read_message(stream: Any) -> dict[str, Any] | None:
    content_length = None
    saw_header_bytes = False
    while True:
        line = stream.readline()
        if not line:
            if saw_header_bytes:
                raise ServiceNowError("Incomplete message header.")
            return None
        saw_header_bytes = True
        if line in (b"\r\n", b"\n"):
            break
        decoded_line = line.decode("utf-8")
        if ":" not in decoded_line:
            raise ServiceNowError("Malformed header line.")
        key, _, value = decoded_line.partition(":")
        if key.lower() == "content-length":
            try:
                content_length = int(value.strip())
            except ValueError as error:
                raise ServiceNowError("Invalid Content-Length header.") from error

    if content_length is None:
        raise ServiceNowError("Missing Content-Length header.")

    body = stream.read(content_length)
    if len(body) != content_length:
        raise ServiceNowError("Incomplete message body.")
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise ServiceNowError("Invalid JSON payload.") from error


def write_message(stream: Any, message: dict[str, Any]) -> None:
    payload = json.dumps(message).encode("utf-8")
    stream.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("utf-8"))
    stream.write(payload)
    stream.flush()


def main() -> int:
    try:
        client = ServiceNowClient(ServiceNowConfig.from_env())
    except ServiceNowError as error:
        print(str(error), file=sys.stderr)
        return 1

    server = ServiceNowMCPServer(client)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    while True:
        try:
            request = read_message(stdin)
        except ServiceNowError as error:
            print(str(error), file=sys.stderr)
            write_message(stdout, error_response(None, -32700, str(error)))
            continue

        if request is None:
            return 0

        response = server.process_request(request)
        if response is not None:
            write_message(stdout, response)


if __name__ == "__main__":
    raise SystemExit(main())
