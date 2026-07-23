# anyvm-mcp

> MCP server for **anyvm** -- boot and manage BSD, illumos, Linux, Haiku, Android, GNU Hurd, and Plan 9 VMs with natural language.
> Works with **Claude Code**, **GitHub Copilot**, and any other [MCP](https://modelcontextprotocol.io)-compatible AI assistant.

---

## Overview

**anyvm-mcp** is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that bridges AI coding assistants with the [anyvm](https://anyvm.org) VM launcher.
anyvm boots prebuilt guest images under QEMU -- no manual installation, no libvirt. Once installed, your AI assistant can:

- Boot **FreeBSD, OpenBSD, NetBSD, DragonFly BSD, GhostBSD, MidnightBSD, Solaris, OmniOS, OpenIndiana, Tribblix, Haiku, Ubuntu, openEuler, BlissOS (Android), Debian GNU/Hurd, and Plan 9** guests in the background
- Target **x86_64, aarch64, riscv64, sparc64, powerpc64, s390x** and more (per-OS availability varies)
- **Execute commands** inside running VMs over SSH for instant diagnostics
- **Read the serial console** to debug boot failures
- Map **ports** and sync **folders** (rsync / sshfs / nfs / scp / 9p) between host and guest
- **Stop** VMs gracefully (guest shutdown with an ACPI fallback) or hard-kill via the QEMU monitor

The VM model mirrors the anyvm CLI: a VM is identified by its OS name plus
optional release (e.g. `freebsd` + `14.3`), not by user-invented names.
The first boot of an OS/release downloads the image from the matching
[anyvm-org builder](https://github.com/anyvm-org) release, so allow several
minutes; later boots reuse the cached image.

---

## Requirements

| Requirement | Notes |
|-------------|-------|
| Python >= 3.10 | |
| QEMU | `qemu-system-*` on PATH (anyvm can also self-download pinned builds for some guests) |
| OpenSSH client | `ssh` on PATH (used for exec/stop) |
| An MCP-compatible AI assistant | Claude Code, GitHub Copilot in VS Code, or any other MCP client |

> **Note:** The `anyvm.py` launcher is bundled automatically -- no separate installation needed.

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

> *"Boot a FreeBSD 14.3 VM with 2 CPUs and 2 GB RAM, then run uname -a in it."*

> *"My OpenBSD VM won't answer SSH. Show me the serial console output and suggest a fix."*

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
usage: anyvm-mcp [-h] [--anyvm PATH] [--data-dir DIR]
                 [--transport {stdio,sse,streamable-http}]
                 [--host HOST] [--port PORT]

options:
  --anyvm PATH          Path to the anyvm launcher (default: bundled anyvm.py)
  --data-dir DIR        Directory for images, logs, and VM state (default: ~/.anyvm-mcp)
  --transport           MCP transport: stdio (default), sse, or streamable-http
  --host HOST           Bind host for HTTP transports (default: 127.0.0.1)
  --port PORT           Bind port for HTTP transports (default: 8000)
```

---

## Available tools

| Tool | Description |
|------|-------------|
| `list_supported_os` | Every guest OS anyvm can boot, with notes and SSH user |
| `start_vm` | Boot a guest in the background (os, release, arch, mem, cpus, ports, volumes, sync) |
| `list_running_vms` | Registered VMs with a live running/stopped probe |
| `vm_info` | State, SSH port/user/key, and serial-log path for one VM |
| `exec_in_vm` | Run a shell command over SSH inside a running VM |
| `stop_vm` | Graceful guest shutdown (SSH + ACPI fallback); `force` hard-kills via QEMU monitor |
| `console_output` | Tail the VM serial console log (works even when SSH is down) |

Notes:

- `start_vm` separates OS and release: `os="freebsd", release="14.3"` -- omit
  `release` for the builder's default release.
- Plan 9 (9front) guests have no SSH; `exec_in_vm` is unavailable for them.
  Use `console_output`, or the VNC web console anyvm prints at boot.
- Haiku's SSH user is `user`; all other guests use `root`.

---

## Example conversations

### Boot and inspect a VM

```
You:      Boot an OmniOS VM with 4 CPUs and 4 GB RAM and show me its uname.
Copilot:  [calls start_vm(os="omnios", cpus=4, mem_mb=4096)]
          VM omnios-r151054 is running (ssh -p 42761 root@127.0.0.1).
          [calls exec_in_vm(os="omnios", command="uname -a")]
          SunOS omnios 5.11 omnios-r151054 i86pc i386 i86pc
```

### Debug a boot failure

```
You:      My NetBSD VM never comes up on SSH. What's wrong?
Copilot:  [calls console_output(os="netbsd", lines=200)]
          The console shows the kernel waiting on root device selection...
```

### Cross-architecture testing

```
You:      Run my test script on Ubuntu riscv64.
Copilot:  [calls start_vm(os="ubuntu", arch="riscv64",
                          volumes=["/home/me/proj:/proj"], sync="rsync")]
          [calls exec_in_vm(os="ubuntu", command="cd /proj && sh run-tests.sh")]
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

MIT -- see [LICENSE](LICENSE).
