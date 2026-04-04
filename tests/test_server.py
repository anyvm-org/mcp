"""Tests for the FastMCP server — verifies tool registration and behaviour.

``server.call_tool`` returns a 2-tuple ``(content_blocks, structured)``,
where ``structured["result"]`` holds the actual return value of the tool
function.  Helper ``_result`` extracts that value for assertions.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from anyvm_skill.server import create_server
from anyvm_skill.vm_manager import AnyvmError, SnapshotInfo, VmInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VM1 = VmInfo(name="vm1", state="running", os="freebsd-14", cpus=2, memory_mb=1024, ip="10.0.0.1")
_VM2 = VmInfo(name="vm2", state="stopped", os="openbsd-7", cpus=1, memory_mb=512)
_SNAP1 = SnapshotInfo(name="snap1", vm_name="vm1", created="2024-01-01")


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
def server():
    return create_server(anyvm_path="/usr/local/bin/anyvm")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify that all expected tools are registered on the server."""

    EXPECTED_TOOLS = {
        "list_vms",
        "vm_info",
        "create_vm",
        "start_vm",
        "stop_vm",
        "destroy_vm",
        "exec_in_vm",
        "console_output",
        "list_snapshots",
        "create_snapshot",
        "restore_snapshot",
        "delete_snapshot",
        "network_info",
    }

    @pytest.mark.asyncio
    async def test_all_tools_registered(self, server):
        tools = await server.list_tools()
        registered = {t.name for t in tools}
        assert self.EXPECTED_TOOLS == registered


# ---------------------------------------------------------------------------
# Tool call tests
# ---------------------------------------------------------------------------


class TestListVmsTool:
    @pytest.mark.asyncio
    async def test_returns_list(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "list_vms", return_value=[_VM1, _VM2]):
            raw = await server.call_tool("list_vms", {})

        result = _result(raw)
        assert isinstance(result, list)
        names = [r["name"] for r in result]
        assert "vm1" in names
        assert "vm2" in names

    @pytest.mark.asyncio
    async def test_returns_error_on_failure(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "list_vms", side_effect=AnyvmError("CLI missing")):
            raw = await server.call_tool("list_vms", {})

        result = _result(raw)
        assert result[0].get("error") is not None


class TestVmInfoTool:
    @pytest.mark.asyncio
    async def test_returns_info(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "vm_info", return_value=_VM1):
            raw = await server.call_tool("vm_info", {"name": "vm1"})

        result = _result(raw)
        assert result["name"] == "vm1"
        assert result["state"] == "running"

    @pytest.mark.asyncio
    async def test_returns_error_on_failure(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "vm_info", side_effect=AnyvmError("not found")):
            raw = await server.call_tool("vm_info", {"name": "missing"})

        result = _result(raw)
        assert "error" in result


class TestCreateVmTool:
    @pytest.mark.asyncio
    async def test_creates_vm(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "create_vm", return_value=_VM1):
            raw = await server.call_tool(
                "create_vm",
                {"name": "vm1", "os": "freebsd-14", "cpus": 2, "memory_mb": 1024, "disk_gb": 20},
            )

        result = _result(raw)
        assert result["name"] == "vm1"

    @pytest.mark.asyncio
    async def test_error_propagated(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "create_vm", side_effect=AnyvmError("name conflict")):
            raw = await server.call_tool("create_vm", {"name": "dup", "os": "freebsd-14"})

        result = _result(raw)
        assert "error" in result


class TestStartStopDestroyTools:
    @pytest.mark.asyncio
    async def test_start_vm(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "start_vm", return_value="VM started"):
            raw = await server.call_tool("start_vm", {"name": "vm1"})
        assert "started" in _result(raw).lower()

    @pytest.mark.asyncio
    async def test_stop_vm(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "stop_vm", return_value="VM stopped"):
            raw = await server.call_tool("stop_vm", {"name": "vm1"})
        assert "stopped" in _result(raw).lower()

    @pytest.mark.asyncio
    async def test_destroy_vm(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "destroy_vm", return_value="destroyed"):
            raw = await server.call_tool("destroy_vm", {"name": "vm1"})
        assert "destroyed" in _result(raw).lower()


class TestExecInVmTool:
    @pytest.mark.asyncio
    async def test_exec(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "exec_in_vm", return_value="FreeBSD 14.0"):
            raw = await server.call_tool(
                "exec_in_vm", {"name": "vm1", "command": "uname -r"}
            )
        assert "FreeBSD" in _result(raw)


class TestSnapshotTools:
    @pytest.mark.asyncio
    async def test_list_snapshots(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "list_snapshots", return_value=[_SNAP1]):
            raw = await server.call_tool("list_snapshots", {"name": "vm1"})
        assert _result(raw)[0]["name"] == "snap1"

    @pytest.mark.asyncio
    async def test_create_snapshot(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "create_snapshot", return_value=_SNAP1):
            raw = await server.call_tool(
                "create_snapshot", {"name": "vm1", "snapshot_name": "snap1"}
            )
        assert _result(raw)["name"] == "snap1"

    @pytest.mark.asyncio
    async def test_restore_snapshot(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "restore_snapshot", return_value="restored"):
            raw = await server.call_tool(
                "restore_snapshot", {"name": "vm1", "snapshot_name": "snap1"}
            )
        assert "restored" in _result(raw).lower()

    @pytest.mark.asyncio
    async def test_delete_snapshot(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(VmManager, "delete_snapshot", return_value="deleted"):
            raw = await server.call_tool(
                "delete_snapshot", {"name": "vm1", "snapshot_name": "snap1"}
            )
        assert "deleted" in _result(raw).lower()


class TestNetworkInfoTool:
    @pytest.mark.asyncio
    async def test_network_info(self, server):
        from anyvm_skill.vm_manager import VmManager
        with patch.object(
            VmManager, "network_info", return_value={"ip": "10.0.0.1", "mac": "aa:bb:cc:00:11:22"}
        ):
            raw = await server.call_tool("network_info", {"name": "vm1"})
        assert _result(raw)["ip"] == "10.0.0.1"
