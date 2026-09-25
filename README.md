# servicenowMCP

A minimal local MCP server for OpenCode that lets `deepseek-v4-flash` inspect ServiceNow records over the standard ServiceNow Table API.

## What this provides

- A local stdio MCP server implemented in Python (`/absolute/path/to/servicenowMCP/servicenow_mcp.py`)
- OpenCode-compatible tools for:
  - `query_records`
  - `get_record`
  - `describe_table`
- No third-party runtime dependencies

## ServiceNow environment

Set one of these authentication modes before starting OpenCode:

### Basic auth

```bash
export SERVICENOW_INSTANCE="your-instance"
export SERVICENOW_USERNAME="your.username"
export SERVICENOW_PASSWORD="your-password"
```

`SERVICENOW_INSTANCE` can be either the instance name (`dev12345`) or a full URL such as `https://dev12345.service-now.com`.

### Token auth

```bash
export SERVICENOW_INSTANCE="your-instance"
export SERVICENOW_TOKEN="your-token"
```

## OpenCode configuration

Add this to your project `opencode.json` or `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "deepseek": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "DeepSeek",
      "options": {
        "baseURL": "https://api.deepseek.com/v1"
      },
      "models": {
        "deepseek-v4-flash": {
          "name": "DeepSeek V4 Flash"
        }
      }
    }
  },
  "model": "deepseek/deepseek-v4-flash",
  "mcp": {
    "servers": {
      "servicenow": {
        "type": "local",
        "command": [
          "python",
          "/absolute/path/to/servicenowMCP/servicenow_mcp.py"
        ]
      }
    }
  }
}
```

If your OpenCode installation uses a different provider identifier, keep the same `deepseek-v4-flash` model name and adjust only the provider prefix.

## Example prompts in OpenCode

- `Use the servicenow query_records tool to list active priority 1 incidents.`
- `Use the servicenow get_record tool to fetch incident <sys_id>.`
- `Use the servicenow describe_table tool to inspect the incident table schema.`

## Local validation

Run the focused test suite:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

You can also verify the server starts and answers an MCP initialize request once your ServiceNow environment variables are set.
