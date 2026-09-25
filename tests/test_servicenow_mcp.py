import io
import json
import unittest

from servicenow_mcp import (
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


if __name__ == "__main__":
    unittest.main()
