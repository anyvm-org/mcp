"""VM management logic — wraps the anyvm CLI for BSD/Illumos VM operations."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any


class AnyvmError(Exception):
    """Raised when an anyvm CLI command fails."""


@dataclass
class VmInfo:
    """Represents a virtual machine managed by anyvm."""

    name: str
    state: str
    os: str = ""
    cpus: int = 1
    memory_mb: int = 512
    ip: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VmInfo":
        return cls(
            name=data.get("name", ""),
            state=data.get("state", "unknown"),
            os=data.get("os", ""),
            cpus=int(data.get("cpus", 1)),
            memory_mb=int(data.get("memory", 512)),
            ip=data.get("ip", ""),
            extra={
                k: v
                for k, v in data.items()
                if k not in {"name", "state", "os", "cpus", "memory", "ip"}
            },
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "state": self.state,
            "os": self.os,
            "cpus": self.cpus,
            "memory_mb": self.memory_mb,
            "ip": self.ip,
        }
        d.update(self.extra)
        return d


@dataclass
class SnapshotInfo:
    """Represents a VM snapshot."""

    name: str
    vm_name: str
    created: str = ""
    description: str = ""

    @classmethod
    def from_dict(cls, vm_name: str, data: dict[str, Any]) -> "SnapshotInfo":
        return cls(
            name=data.get("name", ""),
            vm_name=vm_name,
            created=data.get("created", ""),
            description=data.get("description", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "vm_name": self.vm_name,
            "created": self.created,
            "description": self.description,
        }


class VmManager:
    """Manages BSD/Illumos virtual machines via the anyvm CLI."""

    def __init__(self, anyvm_path: str | None = None) -> None:
        self._anyvm = anyvm_path or shutil.which("anyvm") or "anyvm"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, *args: str, input_text: str | None = None) -> str:
        """Run an anyvm sub-command and return stdout as a string.

        Raises:
            AnyvmError: if the command exits with a non-zero status.
        """
        cmd = [self._anyvm, *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                input=input_text,
            )
        except FileNotFoundError as exc:
            raise AnyvmError(
                f"anyvm CLI not found at '{self._anyvm}'. "
                "Install anyvm and ensure it is on your PATH."
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            detail = stderr or stdout or f"exit code {result.returncode}"
            raise AnyvmError(f"anyvm {' '.join(args)}: {detail}")

        return result.stdout.strip()

    def _run_json(self, *args: str) -> Any:
        """Run an anyvm sub-command and parse its JSON output."""
        raw = self._run(*args, "--format", "json")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AnyvmError(
                f"anyvm returned non-JSON output for '{' '.join(args)}': {raw[:200]}"
            ) from exc

    # ------------------------------------------------------------------
    # VM lifecycle
    # ------------------------------------------------------------------

    def list_vms(self) -> list[VmInfo]:
        """Return all VMs known to anyvm."""
        data = self._run_json("list")
        if not isinstance(data, list):
            data = data.get("vms", [])
        return [VmInfo.from_dict(d) for d in data]

    def create_vm(
        self,
        name: str,
        os: str,
        *,
        cpus: int = 1,
        memory_mb: int = 512,
        disk_gb: int = 20,
        extra_args: list[str] | None = None,
    ) -> VmInfo:
        """Create a new BSD/Illumos VM.

        Args:
            name: Unique name for the VM.
            os: OS image identifier, e.g. ``freebsd-14``, ``openbsd-7``,
                ``omnios-r151050``, ``netbsd-10``.
            cpus: Number of vCPUs.
            memory_mb: RAM in megabytes.
            disk_gb: Root disk size in gigabytes.
            extra_args: Additional raw CLI arguments passed verbatim.
        """
        args = [
            "create",
            name,
            "--os",
            os,
            "--cpus",
            str(cpus),
            "--memory",
            str(memory_mb),
            "--disk",
            str(disk_gb),
        ]
        if extra_args:
            args.extend(extra_args)
        self._run(*args)
        return self.vm_info(name)

    def start_vm(self, name: str) -> str:
        """Start a stopped VM. Returns a status message."""
        return self._run("start", name)

    def stop_vm(self, name: str, *, force: bool = False) -> str:
        """Gracefully (or forcefully) stop a running VM."""
        args = ["stop", name]
        if force:
            args.append("--force")
        return self._run(*args)

    def destroy_vm(self, name: str, *, confirm: bool = True) -> str:
        """Permanently destroy a VM and free its resources.

        Args:
            name: VM name to destroy.
            confirm: When *True* (default) the ``--yes`` flag is passed so the
                     operation is non-interactive.
        """
        args = ["destroy", name]
        if confirm:
            args.append("--yes")
        return self._run(*args)

    def vm_info(self, name: str) -> VmInfo:
        """Return detailed information about a single VM."""
        data = self._run_json("info", name)
        if isinstance(data, list):
            data = data[0]
        return VmInfo.from_dict(data)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def exec_in_vm(self, name: str, command: str) -> str:
        """Run *command* inside the VM and return combined stdout/stderr.

        Args:
            name: VM name.
            command: Shell command to execute (passed to the VM's default shell).
        """
        return self._run("exec", name, "--", command)

    # ------------------------------------------------------------------
    # Logs / console output
    # ------------------------------------------------------------------

    def console_output(self, name: str, *, lines: int = 100) -> str:
        """Return the last *lines* lines of VM console output."""
        return self._run("console", name, "--lines", str(lines), "--no-follow")

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def list_snapshots(self, name: str) -> list[SnapshotInfo]:
        """List all snapshots for the given VM."""
        data = self._run_json("snapshot", "list", name)
        if not isinstance(data, list):
            data = data.get("snapshots", [])
        return [SnapshotInfo.from_dict(name, d) for d in data]

    def create_snapshot(
        self,
        name: str,
        snapshot_name: str,
        *,
        description: str = "",
    ) -> SnapshotInfo:
        """Create a snapshot of a VM.

        Args:
            name: VM name.
            snapshot_name: Name for the new snapshot.
            description: Optional human-readable description.
        """
        args = ["snapshot", "create", name, snapshot_name]
        if description:
            args.extend(["--description", description])
        self._run(*args)
        return SnapshotInfo(name=snapshot_name, vm_name=name, description=description)

    def restore_snapshot(self, name: str, snapshot_name: str) -> str:
        """Restore a VM to a previously created snapshot."""
        return self._run("snapshot", "restore", name, snapshot_name)

    def delete_snapshot(self, name: str, snapshot_name: str) -> str:
        """Delete a snapshot."""
        return self._run("snapshot", "delete", name, snapshot_name)

    # ------------------------------------------------------------------
    # Networking
    # ------------------------------------------------------------------

    def network_info(self, name: str) -> dict[str, Any]:
        """Return network configuration for the VM (IP, MAC, interfaces…)."""
        data = self._run_json("network", name)
        if isinstance(data, list):
            return {"interfaces": data}
        return data
