"""FastMCP server that exposes anyvm VM management as AI-assistant tools.

Supported transports
--------------------
* **stdio** (default) -- works out-of-the-box with Claude Code, GitHub Copilot,
  and any MCP-compatible agent that launches the server as a subprocess.
* **HTTP/SSE** -- run with ``--transport sse --port <port>`` for web-based clients.

Quickstart (stdio)::

    pip install anyvm-mcp
    anyvm-mcp          # starts the MCP server on stdin/stdout

Claude Code (``~/.claude/mcp.json``)::

    {
      "mcpServers": {
        "anyvm": {
          "command": "anyvm-mcp"
        }
      }
    }

VS Code / GitHub Copilot (``.vscode/mcp.json``)::

    {
      "servers": {
        "anyvm": {
          "type": "stdio",
          "command": "anyvm-mcp"
        }
      }
    }
"""

from __future__ import annotations

import argparse
import sys
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from anyvm_mcp.vm_manager import AnyvmError, VmManager

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

_INSTRUCTIONS = """\
You are connected to the anyvm MCP server. anyvm boots prebuilt guest OS
images under QEMU: BSDs (FreeBSD, OpenBSD, NetBSD, DragonFly BSD, GhostBSD,
MidnightBSD), illumos/Solaris (OmniOS, OpenIndiana, Tribblix, Solaris),
Linux (Ubuntu, openEuler), Haiku, BlissOS (Android), Debian GNU/Hurd, and
Plan 9 (9front) -- across x86_64, aarch64, riscv64, sparc64, powerpc64,
s390x and more, depending on the OS.

Workflow:
1. list_supported_os to see what can be booted.
2. start_vm to boot a guest in the background (first boot downloads the
   image, which can take several minutes).
3. exec_in_vm to run commands over SSH; console_output to read the serial
   console when SSH is not (yet) available.
4. stop_vm when done.

A VM is identified by its OS name plus optional release (e.g. os='freebsd',
release='14.3') -- there are no user-invented VM names. Use list_running_vms
to see what is currently registered. Note: plan9 has no SSH; use the serial
console for it.
"""


def create_server(
    anyvm_path: str | None = None,
    data_dir: str | None = None,
) -> FastMCP:
    """Build and return the FastMCP server instance.

    Args:
        anyvm_path: Optional explicit path to the ``anyvm`` launcher
            (binary or anyvm.py). When *None* the bundled anyvm.py is used,
            falling back to ``anyvm`` on ``PATH``.
        data_dir: Directory for images, logs, and the VM registry
            (default: ``~/.anyvm-mcp``).
    """
    mcp: FastMCP = FastMCP(
        name="anyvm-mcp",
        instructions=_INSTRUCTIONS,
    )
    mgr = VmManager(anyvm_path=anyvm_path, data_dir=data_dir)

    # ------------------------------------------------------------------
    # Tool: list_supported_os
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "List every guest OS anyvm can boot (BSDs, illumos, Linux, Haiku, "
            "Android/BlissOS, GNU Hurd, Plan 9), with notes and the SSH user."
        )
    )
    def list_supported_os() -> list[dict]:
        """List supported guest operating systems."""
        return mgr.list_supported_os()

    # ------------------------------------------------------------------
    # Tool: start_vm
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Boot a guest VM in the background. The first boot of an "
            "OS/release downloads the image (hundreds of MB -- allow several "
            "minutes). Returns SSH connection details on success. "
            "OS and release are separate: os='freebsd' release='14.3', "
            "not 'freebsd-14.3'. Omit release to get the default release."
        )
    )
    def start_vm(
        os: Annotated[str, "Guest OS name, e.g. 'freebsd', 'openbsd', 'ubuntu'"],
        release: Annotated[str, "OS release, e.g. '14.3' (default: builder default)"] = "",
        arch: Annotated[
            str, "CPU arch: x86_64, aarch64, riscv64, ... (default: host arch)"
        ] = "",
        mem_mb: Annotated[int, "RAM in MB (0 = anyvm default)"] = 0,
        cpus: Annotated[int, "vCPU count (0 = anyvm default)"] = 0,
        ports: Annotated[
            list[str], "Port forwards, each 'host:guest' or 'udp:host:guest'"
        ] = [],
        volumes: Annotated[
            list[str], "Folder syncs, each '/host/path:/guest/path'"
        ] = [],
        sync: Annotated[
            str,
            "Sync mode for volumes: rsync, sshfs, nfs, sys-nfs, scp, 9p "
            "(default: anyvm's per-OS default)",
        ] = "",
        snapshot_mode: Annotated[
            bool, "QEMU snapshot mode: all guest changes are discarded"
        ] = False,
        boot_timeout_sec: Annotated[
            int, "Seconds to allow for download + boot (default 1800)"
        ] = 1800,
    ) -> dict:
        """Boot a guest VM detached and register it."""
        try:
            info = mgr.start_vm(
                os,
                release=release,
                arch=arch,
                mem_mb=mem_mb,
                cpus=cpus,
                ports=list(ports) if ports else None,
                volumes=list(volumes) if volumes else None,
                sync=sync,
                snapshot_mode=snapshot_mode,
                boot_timeout_sec=boot_timeout_sec,
            )
            return info.to_dict()
        except AnyvmError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool: list_running_vms
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "List all VMs this server has booted, with a live running/stopped "
            "state probe, SSH ports, and log locations."
        )
    )
    def list_running_vms() -> list[dict]:
        """List registered VMs and their live state."""
        try:
            return [v.to_dict() for v in mgr.list_vms()]
        except AnyvmError as exc:
            return [{"error": str(exc)}]

    # ------------------------------------------------------------------
    # Tool: vm_info
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Detailed info about one VM (state, SSH port/user/key, serial log "
            "path). Identify the VM by OS name and optional release."
        )
    )
    def vm_info(
        os: Annotated[str, "Guest OS name, e.g. 'freebsd'"],
        release: Annotated[str, "OS release (needed only if several releases run)"] = "",
    ) -> dict:
        """Return detailed information about a VM."""
        try:
            return mgr.vm_info(os, release).to_dict()
        except AnyvmError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool: exec_in_vm
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Execute a shell command over SSH inside a running VM and return "
            "the combined stdout/stderr. Not available for plan9 (no SSH)."
        )
    )
    def exec_in_vm(
        os: Annotated[str, "Guest OS name, e.g. 'freebsd'"],
        command: Annotated[str, "Shell command to run inside the VM"],
        release: Annotated[str, "OS release (needed only if several releases run)"] = "",
        timeout_sec: Annotated[int, "Command timeout in seconds (default 120)"] = 120,
    ) -> str:
        """Run a command inside a VM."""
        try:
            return mgr.exec_in_vm(
                os, command, release=release, timeout_sec=timeout_sec
            )
        except AnyvmError as exc:
            return "Error: {}".format(exc)

    # ------------------------------------------------------------------
    # Tool: stop_vm
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Stop a running VM: graceful guest shutdown over SSH with an ACPI "
            "fallback; force=true hard-kills QEMU via its monitor."
        )
    )
    def stop_vm(
        os: Annotated[str, "Guest OS name, e.g. 'freebsd'"],
        release: Annotated[str, "OS release (needed only if several releases run)"] = "",
        force: Annotated[bool, "Hard-stop via QEMU monitor (default: false)"] = False,
    ) -> str:
        """Stop a VM."""
        try:
            return mgr.stop_vm(os, release=release, force=force)
        except AnyvmError as exc:
            return "Error: {}".format(exc)

    # ------------------------------------------------------------------
    # Tool: console_output
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "Tail the VM's serial console log. Works even when SSH is down -- "
            "the tool for debugging boot failures, and the only way to see "
            "plan9 output."
        )
    )
    def console_output(
        os: Annotated[str, "Guest OS name, e.g. 'freebsd'"],
        release: Annotated[str, "OS release (needed only if several releases run)"] = "",
        lines: Annotated[int, "Number of log lines to return (default 100)"] = 100,
    ) -> str:
        """Get VM serial console output."""
        try:
            return mgr.console_output(os, release=release, lines=lines)
        except AnyvmError as exc:
            return "Error: {}".format(exc)

    return mcp


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the ``anyvm-mcp`` command."""
    parser = argparse.ArgumentParser(
        prog="anyvm-mcp",
        description=(
            "MCP server that lets AI assistants boot and manage guest VMs "
            "(BSD, illumos, Linux, Haiku, Android, Hurd, Plan 9) via anyvm."
        ),
    )
    parser.add_argument(
        "--anyvm",
        metavar="PATH",
        default=None,
        help="Path to the anyvm launcher (default: bundled anyvm.py).",
    )
    parser.add_argument(
        "--data-dir",
        metavar="DIR",
        default=None,
        help="Directory for images, logs, and VM state (default: ~/.anyvm-mcp).",
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

    server = create_server(anyvm_path=args.anyvm, data_dir=args.data_dir)

    if args.transport == "stdio":
        server.run(transport="stdio")
    elif args.transport == "sse":
        server.run(transport="sse", host=args.host, port=args.port)
    elif args.transport == "streamable-http":
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        print("Unknown transport: {}".format(args.transport), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
