"""anyvm-mcp: MCP server for anyvm VM management — run, manage, and debug BSD/Illumos VMs."""

from anyvm_mcp.server import create_server, main

__all__ = ["create_server", "main"]
__version__ = "0.1.0"
