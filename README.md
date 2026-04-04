# anyvm-mcp

> MCP server for **anyvm** — run, manage, and debug BSD/Illumos VMs with natural language.  
> Works with **Claude Code**, **GitHub Copilot**, and any other [MCP](https://modelcontextprotocol.io)-compatible AI assistant.

---

## Overview

**anyvm-mcp** is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that bridges AI coding assistants with the [anyvm](https://anyvm.org) VM manager.  
Once installed, your AI assistant can:

- 🖥️ **Create** FreeBSD, OpenBSD, NetBSD, OmniOS, and other BSD/Illumos VMs in seconds
- 🚀 **Start / Stop / Destroy** VMs on demand
- 🔍 **Inspect** VM state, IPs, CPU, memory, and network configuration
- 💻 **Execute commands** inside running VMs for instant diagnostics
- 📜 **Read console output** to debug boot failures or system errors
- 📸 **Snapshot and restore** VMs to safe checkpoints

---

## Requirements

| Requirement | Notes |
|-------------|-------|
| Python ≥ 3.10 | |
| An MCP-compatible AI assistant | Claude Code, GitHub Copilot in VS Code, or any other MCP client |

> **Note:** The `anyvm` CLI is bundled automatically — no separate installation needed.

---

## Installation

```bash
pipx install anyvm-mcp
```

Or with pip:

```bash
pip install anyvm-mcp
```

Or install from source:

```bash
git clone https://github.com/anyvm-org/mcp
cd mcp
pip install -e .
```

---

## Quickstart

### Claude Code

Add the server to `~/.claude/mcp.json` (or the project-level `.claude/mcp.json`):

```json
{
  "mcpServers": {
    "anyvm": {
      "command": "anyvm-mcp"
    }
  }
}
```

Restart Claude Code. You can now say things like:

> *"Create a FreeBSD 14 VM called 'dev' with 2 CPUs and 2 GB RAM, then show me its IP."*

> *"My OpenBSD VM won't boot. Show me the console output and suggest a fix."*

### GitHub Copilot (VS Code)

Add the server to `.vscode/mcp.json` in your workspace (or the user-level settings):

```json
{
  "servers": {
    "anyvm": {
      "type": "stdio",
      "command": "anyvm-mcp"
    }
  }
}
```

### Generic MCP client (HTTP/SSE)

Run anyvm-mcp as an HTTP server:

```bash
anyvm-mcp --transport sse --host 127.0.0.1 --port 8000
```

Then point your MCP client at `http://127.0.0.1:8000/sse`.

---

## CLI reference

```
usage: anyvm-mcp [-h] [--anyvm PATH] [--transport {stdio,sse,streamable-http}]
                   [--host HOST] [--port PORT]

options:
  --anyvm PATH          Path to the anyvm binary (default: bundled version)
  --transport           MCP transport: stdio (default), sse, or streamable-http
  --host HOST           Bind host for HTTP transports (default: 127.0.0.1)
  --port PORT           Bind port for HTTP transports (default: 8000)
```

---

## Available tools

| Tool | Description |
|------|-------------|
| `list_vms` | List all VMs with state, OS, CPU, RAM, and IP |
| `vm_info` | Detailed info about a single VM |
| `create_vm` | Create a new BSD/Illumos VM |
| `start_vm` | Start a stopped VM |
| `stop_vm` | Gracefully (or forcefully) stop a running VM |
| `destroy_vm` | Permanently delete a VM and free its resources |
| `exec_in_vm` | Run a shell command inside a VM |
| `console_output` | Fetch the latest VM serial/console log |
| `list_snapshots` | List snapshots for a VM |
| `create_snapshot` | Snapshot a VM at its current state |
| `restore_snapshot` | Roll a VM back to a snapshot |
| `delete_snapshot` | Delete a snapshot |
| `network_info` | IPs, MACs, and virtual NIC configuration |

---

## Example conversations

### Creating and inspecting a VM

```
You:      Create an OmniOS VM called "omnios-test" with 4 CPUs, 4 GB RAM, and 40 GB disk.
Copilot:  ✅ VM "omnios-test" created. State: stopped.
          Run `start_vm` to boot it.

You:      Start it and tell me its IP address.
Copilot:  ✅ VM started. IP: 192.168.100.42
```

### Debugging a boot failure

```
You:      My FreeBSD VM "bsd-web" won't respond to SSH. What's wrong?
Copilot:  [calls console_output("bsd-web", lines=200)]
          The console log shows a panic in vtnet0 — likely a driver issue.
          Try: exec_in_vm("bsd-web", "sysctl hw.vtnet.0.csum_disable=1")
```

### Snapshot workflow

```
You:      Snapshot "bsd-web" before I upgrade it.
Copilot:  [calls create_snapshot("bsd-web", "pre-upgrade", description="before pkg upgrade")]
          ✅ Snapshot "pre-upgrade" created.

You:      The upgrade broke something. Roll back.
Copilot:  [calls restore_snapshot("bsd-web", "pre-upgrade")]
          ✅ Restored to snapshot "pre-upgrade".
```

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run a single test file
pytest tests/test_vm_manager.py -v
```

---

## License

MIT — see [LICENSE](LICENSE).
