"""FastMCP server that exposes anyvm VM management as AI-assistant tools.

Supported transports
--------------------
* **stdio** (default) — works out-of-the-box with Claude Code, GitHub Copilot,
  and any MCP-compatible agent that launches the server as a subprocess.
* **HTTP/SSE** — run with ``--transport sse --port <port>`` for web-based clients.

Quickstart (stdio)::

    pip install anyvm-skill
    anyvm-skill          # starts the MCP server on stdin/stdout

Claude Code (``~/.claude/mcp.json``)::

    {
      "mcpServers": {
        "anyvm": {
          "command": "anyvm-skill"
        }
      }
    }

VS Code / GitHub Copilot (``.vscode/mcp.json``)::

    {
      "servers": {
        "anyvm": {
          "type": "stdio",
          "command": "anyvm-skill"
        }
      }
    }
"""

from __future__ import annotations

import argparse
import sys
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from anyvm_skill.vm_manager import AnyvmError, VmManager

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

_INSTRUCTIONS = """\
You are connected to the anyvm skill. Use the provided tools to run, manage,
and debug BSD/Illumos virtual machines via anyvm. Prefer the most targeted
tool for each task (e.g. use exec_in_vm to run a single diagnostic command
rather than opening a full interactive session).

Supported guest operating systems include FreeBSD, OpenBSD, NetBSD, OmniOS,
SmartOS, and other BSD/Illumos distributions supported by your anyvm setup.

When something goes wrong, use console_output and exec_in_vm to gather
diagnostic information before suggesting fixes.
"""


def create_server(anyvm_path: str | None = None) -> FastMCP:
    """Build and return the FastMCP server instance.

    Args:
        anyvm_path: Optional explicit path to the ``anyvm`` binary.  When
            *None* the binary is looked up on ``PATH``.
    """
    mcp: FastMCP = FastMCP(
        name="anyvm-skill",
        instructions=_INSTRUCTIONS,
    )
    mgr = VmManager(anyvm_path=anyvm_path)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _ok(message: str) -> str:
        return message if message else "OK"

    def _vm_err(exc: AnyvmError) -> str:
        return f"Error: {exc}"

    # ------------------------------------------------------------------
    # Tool: list_vms
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "List all BSD/Illumos virtual machines managed by anyvm, "
            "including their state (running, stopped, …), OS, CPU, memory, and IP."
        )
    )
    def list_vms() -> list[dict]:
        """List all VMs managed by anyvm."""
        try:
            return [v.to_dict() for v in mgr.list_vms()]
        except AnyvmError as exc:
            return [{"error": str(exc)}]

    # ------------------------------------------------------------------
    # Tool: vm_info
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Get detailed information (state, OS, CPUs, memory, IP, disk, …) "
            "about a single VM."
        )
    )
    def vm_info(
        name: Annotated[str, "Name of the VM to inspect"],
    ) -> dict:
        """Return detailed information about a VM."""
        try:
            return mgr.vm_info(name).to_dict()
        except AnyvmError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool: create_vm
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Create a new BSD/Illumos virtual machine. "
            "Supported OS values include: freebsd-14, freebsd-13, openbsd-7, "
            "netbsd-10, omnios-r151050, smartos. "
            "Returns the new VM's details on success."
        )
    )
    def create_vm(
        name: Annotated[str, "Unique name for the new VM (alphanumeric and hyphens)"],
        os: Annotated[
            str,
            "OS image identifier, e.g. 'freebsd-14', 'openbsd-7', 'omnios-r151050'",
        ],
        cpus: Annotated[int, "Number of virtual CPUs (default: 1)"] = 1,
        memory_mb: Annotated[int, "RAM in megabytes (default: 512)"] = 512,
        disk_gb: Annotated[int, "Root disk size in gigabytes (default: 20)"] = 20,
    ) -> dict:
        """Create a new BSD/Illumos VM."""
        try:
            info = mgr.create_vm(
                name, os, cpus=cpus, memory_mb=memory_mb, disk_gb=disk_gb
            )
            return info.to_dict()
        except AnyvmError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool: start_vm
    # ------------------------------------------------------------------

    @mcp.tool(description="Start a stopped BSD/Illumos VM.")
    def start_vm(
        name: Annotated[str, "Name of the VM to start"],
    ) -> str:
        """Start a VM."""
        try:
            return _ok(mgr.start_vm(name))
        except AnyvmError as exc:
            return _vm_err(exc)

    # ------------------------------------------------------------------
    # Tool: stop_vm
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Stop a running BSD/Illumos VM. "
            "Set force=true to hard-stop without a graceful shutdown."
        )
    )
    def stop_vm(
        name: Annotated[str, "Name of the VM to stop"],
        force: Annotated[
            bool, "Hard-stop without graceful shutdown (default: false)"
        ] = False,
    ) -> str:
        """Stop a VM."""
        try:
            return _ok(mgr.stop_vm(name, force=force))
        except AnyvmError as exc:
            return _vm_err(exc)

    # ------------------------------------------------------------------
    # Tool: destroy_vm
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Permanently destroy a VM and release all its resources (disk, network, …). "
            "This action is irreversible. The VM must be stopped first."
        )
    )
    def destroy_vm(
        name: Annotated[str, "Name of the VM to destroy"],
    ) -> str:
        """Destroy a VM permanently."""
        try:
            return _ok(mgr.destroy_vm(name))
        except AnyvmError as exc:
            return _vm_err(exc)

    # ------------------------------------------------------------------
    # Tool: exec_in_vm
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Execute a shell command inside a running BSD/Illumos VM and return "
            "the combined stdout/stderr output. Useful for diagnostics and automation."
        )
    )
    def exec_in_vm(
        name: Annotated[str, "Name of the VM"],
        command: Annotated[str, "Shell command to run inside the VM"],
    ) -> str:
        """Run a command inside a VM."""
        try:
            return mgr.exec_in_vm(name, command)
        except AnyvmError as exc:
            return _vm_err(exc)

    # ------------------------------------------------------------------
    # Tool: console_output
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Retrieve the latest console (serial) output from a BSD/Illumos VM. "
            "Useful for debugging boot failures or checking system logs."
        )
    )
    def console_output(
        name: Annotated[str, "Name of the VM"],
        lines: Annotated[
            int, "Number of log lines to retrieve (default: 100)"
        ] = 100,
    ) -> str:
        """Get VM console/serial output."""
        try:
            return mgr.console_output(name, lines=lines)
        except AnyvmError as exc:
            return _vm_err(exc)

    # ------------------------------------------------------------------
    # Tool: list_snapshots
    # ------------------------------------------------------------------

    @mcp.tool(
        description="List all snapshots for a BSD/Illumos VM."
    )
    def list_snapshots(
        name: Annotated[str, "Name of the VM"],
    ) -> list[dict]:
        """List snapshots for a VM."""
        try:
            return [s.to_dict() for s in mgr.list_snapshots(name)]
        except AnyvmError as exc:
            return [{"error": str(exc)}]

    # ------------------------------------------------------------------
    # Tool: create_snapshot
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Create a snapshot of a BSD/Illumos VM at its current state. "
            "The VM can be running or stopped."
        )
    )
    def create_snapshot(
        name: Annotated[str, "Name of the VM to snapshot"],
        snapshot_name: Annotated[str, "Name for the new snapshot"],
        description: Annotated[
            str, "Optional human-readable description of the snapshot"
        ] = "",
    ) -> dict:
        """Create a VM snapshot."""
        try:
            snap = mgr.create_snapshot(name, snapshot_name, description=description)
            return snap.to_dict()
        except AnyvmError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool: restore_snapshot
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Restore a BSD/Illumos VM to a previously created snapshot. "
            "The VM will be reverted to the exact state at snapshot time."
        )
    )
    def restore_snapshot(
        name: Annotated[str, "Name of the VM"],
        snapshot_name: Annotated[str, "Name of the snapshot to restore"],
    ) -> str:
        """Restore a VM snapshot."""
        try:
            return _ok(mgr.restore_snapshot(name, snapshot_name))
        except AnyvmError as exc:
            return _vm_err(exc)

    # ------------------------------------------------------------------
    # Tool: delete_snapshot
    # ------------------------------------------------------------------

    @mcp.tool(description="Delete a VM snapshot.")
    def delete_snapshot(
        name: Annotated[str, "Name of the VM"],
        snapshot_name: Annotated[str, "Name of the snapshot to delete"],
    ) -> str:
        """Delete a VM snapshot."""
        try:
            return _ok(mgr.delete_snapshot(name, snapshot_name))
        except AnyvmError as exc:
            return _vm_err(exc)

    # ------------------------------------------------------------------
    # Tool: network_info
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Return network configuration for a BSD/Illumos VM: "
            "IP addresses, MAC addresses, virtual NIC names, and VLAN info."
        )
    )
    def network_info(
        name: Annotated[str, "Name of the VM"],
    ) -> dict:
        """Get VM network configuration."""
        try:
            return mgr.network_info(name)
        except AnyvmError as exc:
            return {"error": str(exc)}

    return mcp


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the ``anyvm-skill`` command."""
    parser = argparse.ArgumentParser(
        prog="anyvm-skill",
        description="MCP server that lets AI assistants manage BSD/Illumos VMs via anyvm.",
    )
    parser.add_argument(
        "--anyvm",
        metavar="PATH",
        default=None,
        help="Path to the anyvm binary (default: looked up on PATH).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport to use (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for HTTP transports (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for HTTP transports (default: 8000).",
    )
    args = parser.parse_args()

    server = create_server(anyvm_path=args.anyvm)

    if args.transport == "stdio":
        server.run(transport="stdio")
    elif args.transport == "sse":
        server.run(transport="sse", host=args.host, port=args.port)
    elif args.transport == "streamable-http":
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        print(f"Unknown transport: {args.transport}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
