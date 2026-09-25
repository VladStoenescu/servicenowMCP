import io
import json
import os
import unittest
from unittest import mock

from servicenow_mcp import (
    ServiceNowClient,
    ServiceNowConfig,
    ServiceNowError,
    ServiceNowMCPServer,
    normalize_instance_url,
    read_message,
    write_message,
)


class FakeClient:
    def query_records(self, **kwargs):
        return {"tool": "query_records", **kwargs}

    def get_record(self, **kwargs):
        return {"tool": "get_record", **kwargs}

    def describe_table(self, table):
        return {"tool": "describe_table", "table": table}


class PagingClient(ServiceNowClient):
    def __init__(self):
        super().__init__(ServiceNowConfig(base_url="https://example.service-now.com", token="token"))
        self.calls = []

    def request_json(self, path, params=None):
        self.calls.append((path, params))
        if path == "/api/now/table/sys_db_object":
            return {"result": [{"name": "incident", "label": "Incident"}]}
        if path == "/api/now/table/sys_dictionary":
            offset = params["sysparm_offset"]
            limit = params["sysparm_limit"]
            if offset == 0:
                return {"result": [{"element": f"field_{index}"} for index in range(limit)]}
            return {"result": [{"element": "field_100"}, {"element": "field_101"}]}
        raise AssertionError(f"Unexpected path: {path}")


class NormalizeInstanceUrlTests(unittest.TestCase):
    def test_adds_https_and_default_domain(self):
        self.assertEqual(
            normalize_instance_url("dev12345"),
            "https://dev12345.service-now.com",
        )

    def test_keeps_full_url(self):
        self.assertEqual(
            normalize_instance_url("https://example.service-now.com/"),
            "https://example.service-now.com",
        )

    def test_config_preserves_token_auth(self):
        env = {
            "SERVICENOW_INSTANCE": "dev12345",
            "SERVICENOW_TOKEN": "token-value",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = ServiceNowConfig.from_env()

        self.assertEqual(config.base_url, "https://dev12345.service-now.com")
        self.assertEqual(config.token, "token-value")
        self.assertIsNone(config.username)
        self.assertIsNone(config.password)


class MCPServerTests(unittest.TestCase):
    def setUp(self):
        self.server = ServiceNowMCPServer(FakeClient())

    def test_list_tools_exposes_expected_tools(self):
        response = self.server.process_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tool_names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(tool_names, ["query_records", "get_record", "describe_table"])

    def test_query_records_tool_dispatches(self):
        response = self.server.process_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "query_records",
                    "arguments": {
                        "table": "incident",
                        "query": "active=true",
                        "fields": ["number", "short_description"],
                        "limit": 5,
                        "offset": 10,
                    },
                },
            }
        )
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["table"], "incident")
        self.assertEqual(payload["query"], "active=true")
        self.assertEqual(payload["fields"], ["number", "short_description"])
        self.assertEqual(payload["limit"], 5)
        self.assertEqual(payload["offset"], 10)

    def test_invalid_limit_returns_tool_error(self):
        response = self.server.process_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "query_records", "arguments": {"table": "incident", "limit": 0}},
            }
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("limit must be between 1 and 100", response["result"]["content"][0]["text"])


class FramingTests(unittest.TestCase):
    def test_write_then_read_message_round_trip(self):
        output = io.BytesIO()
        message = {"jsonrpc": "2.0", "id": 99, "result": {"ok": True}}
        write_message(output, message)
        output.seek(0)
        self.assertEqual(read_message(output), message)


class ServiceNowClientTests(unittest.TestCase):
    def test_describe_table_paginates_dictionary_results(self):
        client = PagingClient()

        payload = client.describe_table("incident")

        self.assertEqual(payload["details"]["name"], "incident")
        self.assertEqual(len(payload["columns"]), 102)
        dictionary_calls = [call for call in client.calls if call[0] == "/api/now/table/sys_dictionary"]
        self.assertEqual([call[1]["sysparm_offset"] for call in dictionary_calls], [0, 100])


class FramingErrorTests(unittest.TestCase):
    def test_invalid_content_length_raises_servicenow_error(self):
        stream = io.BytesIO(b"Content-Length: nope\r\n\r\n{}")
        with self.assertRaises(ServiceNowError):
            read_message(stream)

    def test_incomplete_body_raises_servicenow_error(self):
        stream = io.BytesIO(b"Content-Length: 10\r\n\r\n{}")
        with self.assertRaises(ServiceNowError):
            read_message(stream)


if __name__ == "__main__":
    unittest.main()
