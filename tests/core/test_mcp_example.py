import json
from pathlib import Path

from jurisync import registry

EXAMPLE = Path(__file__).resolve().parents[2] / ".mcp.json.example"


def test_the_example_serves_every_connector_portably():
    servers = json.loads(EXAMPLE.read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {*registry.CONNECTORS, "filing"}
    for name, server in servers.items():
        assert server["command"] == "uv", name
        assert server["args"][-2:][0] == "-m", name
        assert ".venv" not in json.dumps(server), name
        assert "JURISYNC_CUSTOMER" not in server["env"], name  # the office comes from .env, never from this file
