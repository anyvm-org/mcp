"""anyvm-mcp: MCP server for anyvm -- boot and manage BSD, illumos, Linux,
Haiku, Android, GNU Hurd, and Plan 9 guest VMs."""

from anyvm_mcp.server import create_server, main

__all__ = ["create_server", "main"]
__version__ = "0.1.0"
