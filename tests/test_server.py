"""Tests for the FastMCP server -- verifies tool registration and behaviour.

``server.call_tool`` returns a 2-tuple ``(content_blocks, structured)``,
where ``structured["result"]`` holds the actual return value of the tool
function.  Helper ``_result`` extracts that value for assertions.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from anyvm_mcp.server import create_server
from anyvm_mcp.vm_manager import AnyvmError, VmInfo, VmManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VM1 = VmInfo(
    name="freebsd-14.3", os="freebsd", release="14.3", state="running",
    ssh_port=10022, mon_port=10023, user="root",
)
_VM2 = VmInfo(
    name="openbsd-7.9", os="openbsd", release="7.9", state="stopped",
    ssh_port=11022, mon_port=11023, user="root",
)


def _result(call_tool_return):
    """Extract the tool function's return value from call_tool's response.

    FastMCP returns a 2-tuple ``(content, structured)`` for tools with ``list``
    or ``str`` return annotations (structured output enabled), and a plain
    ``list[TextContent]`` for tools annotated with ``dict`` or ``Any``
    (unstructured output).  This helper normalises both forms.
    """
    if isinstance(call_tool_return, tuple):
        _content, structured = call_tool_return
        return structured["result"]
    # Unstructured: parse the JSON-encoded text from the first ContentBlock.
    import json
    return json.loads(call_tool_return[0].text)


@pytest.fixture()
def server(tmp_path):
    return create_server(anyvm_path="/opt/anyvm/anyvm.py", data_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify that all expected tools are registered on the server."""

    EXPECTED_TOOLS = {
        "list_supported_os",
        "start_vm",
        "list_running_vms",
        "vm_info",
        "exec_in_vm",
        "stop_vm",
        "console_output",
    }

    @pytest.mark.asyncio
    async def test_all_tools_registered(self, server):
        tools = await server.list_tools()
        registered = {t.name for t in tools}
        assert self.EXPECTED_TOOLS == registered


# ---------------------------------------------------------------------------
# Tool call tests
# ---------------------------------------------------------------------------


class TestListSupportedOsTool:
    @pytest.mark.asyncio
    async def test_lists_all_os(self, server):
        raw = await server.call_tool("list_supported_os", {})
        result = _result(raw)
        names = {r["os"] for r in result}
        assert "freebsd" in names
        assert "plan9" in names
        assert "hurd" in names
        assert "openeuler" in names
        assert "smartos" not in names


class TestStartVmTool:
    @pytest.mark.asyncio
    async def test_starts_vm(self, server):
        with patch.object(VmManager, "start_vm", return_value=_VM1) as start:
            raw = await server.call_tool(
                "start_vm",
                {"os": "freebsd", "release": "14.3", "mem_mb": 2048, "cpus": 2},
            )
        result = _result(raw)
        assert result["name"] == "freebsd-14.3"
        assert result["state"] == "running"
        assert result["ssh_command"] == "ssh -p 10022 root@127.0.0.1"
        kwargs = start.call_args
        assert kwargs[0][0] == "freebsd"
        assert kwargs[1]["release"] == "14.3"
        assert kwargs[1]["mem_mb"] == 2048

    @pytest.mark.asyncio
    async def test_error_returned_as_dict(self, server):
        with patch.object(
            VmManager, "start_vm", side_effect=AnyvmError("boot failed")
        ):
            raw = await server.call_tool("start_vm", {"os": "freebsd"})
        result = _result(raw)
        assert "boot failed" in result["error"]


class TestListRunningVmsTool:
    @pytest.mark.asyncio
    async def test_returns_list(self, server):
        with patch.object(VmManager, "list_vms", return_value=[_VM1, _VM2]):
            raw = await server.call_tool("list_running_vms", {})
        result = _result(raw)
        names = [r["name"] for r in result]
        assert names == ["freebsd-14.3", "openbsd-7.9"]

    @pytest.mark.asyncio
    async def test_empty(self, server):
        with patch.object(VmManager, "list_vms", return_value=[]):
            raw = await server.call_tool("list_running_vms", {})
        assert _result(raw) == []


class TestVmInfoTool:
    @pytest.mark.asyncio
    async def test_returns_info(self, server):
        with patch.object(VmManager, "vm_info", return_value=_VM1):
            raw = await server.call_tool("vm_info", {"os": "freebsd"})
        result = _result(raw)
        assert result["name"] == "freebsd-14.3"
        assert result["state"] == "running"

    @pytest.mark.asyncio
    async def test_returns_error_on_failure(self, server):
        with patch.object(
            VmManager, "vm_info", side_effect=AnyvmError("start_vm first")
        ):
            raw = await server.call_tool("vm_info", {"os": "openbsd"})
        result = _result(raw)
        assert "error" in result


class TestExecInVmTool:
    @pytest.mark.asyncio
    async def test_exec(self, server):
        with patch.object(
            VmManager, "exec_in_vm", return_value="FreeBSD 14.3-RELEASE"
        ) as ex:
            raw = await server.call_tool(
                "exec_in_vm", {"os": "freebsd", "command": "uname -sr"}
            )
        assert "FreeBSD" in _result(raw)
        assert ex.call_args[0] == ("freebsd", "uname -sr")

    @pytest.mark.asyncio
    async def test_error_returned_as_text(self, server):
        with patch.object(
            VmManager, "exec_in_vm", side_effect=AnyvmError("not running")
        ):
            raw = await server.call_tool(
                "exec_in_vm", {"os": "freebsd", "command": "uname"}
            )
        assert "Error: not running" in _result(raw)


class TestStopVmTool:
    @pytest.mark.asyncio
    async def test_stop(self, server):
        with patch.object(
            VmManager, "stop_vm", return_value="VM 'freebsd-14.3' stopped."
        ) as stop:
            raw = await server.call_tool("stop_vm", {"os": "freebsd"})
        assert "stopped" in _result(raw)
        assert stop.call_args[1]["force"] is False

    @pytest.mark.asyncio
    async def test_stop_force(self, server):
        with patch.object(VmManager, "stop_vm", return_value="stopped") as stop:
            await server.call_tool("stop_vm", {"os": "freebsd", "force": True})
        assert stop.call_args[1]["force"] is True


class TestConsoleOutputTool:
    @pytest.mark.asyncio
    async def test_console(self, server):
        with patch.object(
            VmManager, "console_output", return_value="login: root"
        ) as con:
            raw = await server.call_tool(
                "console_output", {"os": "freebsd", "lines": 50}
            )
        assert "login" in _result(raw)
        assert con.call_args[1]["lines"] == 50
