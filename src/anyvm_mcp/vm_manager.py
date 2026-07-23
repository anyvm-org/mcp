"""VM management logic -- wraps the anyvm launcher (anyvm.py) for MCP tools.

anyvm.py is a one-shot QEMU VM launcher, not a daemon: each invocation
downloads (or reuses) a prebuilt guest image and boots it. With --detach the
launcher process exits after boot while QEMU keeps running in the background.

This manager builds on that model:

* ``start_vm`` invokes ``anyvm.py --os <os> ... --detach`` with an SSH port
  and a QEMU-monitor port picked by the manager, then records the booted VM
  in a small JSON registry under the data directory.
* ``exec_in_vm`` / ``stop_vm`` / ``console_output`` operate on registry
  entries using the artifacts anyvm leaves on disk: the ``<vm>-host.id_rsa``
  SSH key, the ``<vm>.serial.log`` console log, and the QEMU monitor port.
* A VM is identified by its anyvm image name ``<os>[-<release>]`` -- there
  are no user-invented VM names, mirroring the anyvm CLI itself.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any


class AnyvmError(Exception):
    """Raised when an anyvm operation fails."""


# ---------------------------------------------------------------------------
# Static capability tables (mirrors anyvm.py v0.5.x; keep in sync when
# bumping the vendored anyvm version in pyproject.toml)
# ---------------------------------------------------------------------------

# Guest OS names accepted by ``anyvm.py --os`` (see DEFAULT_BUILDER_VERSIONS
# in anyvm.py). Values are short human-readable notes surfaced by the
# list_supported_os tool.
SUPPORTED_OS: dict[str, str] = {
    "freebsd": "FreeBSD (x86_64/aarch64 and more; see freebsd-builder releases)",
    "openbsd": "OpenBSD (x86_64/aarch64; desktop releases like 7.9-xfce available)",
    "netbsd": "NetBSD (x86_64/aarch64/sparc64/riscv64)",
    "dragonflybsd": "DragonFly BSD (x86_64)",
    "ghostbsd": "GhostBSD desktop (FreeBSD-based; MATE/XFCE releases)",
    "midnightbsd": "MidnightBSD (x86_64)",
    "solaris": "Oracle Solaris (x86_64)",
    "omnios": "OmniOS (illumos, x86_64)",
    "openindiana": "OpenIndiana (illumos, x86_64)",
    "tribblix": "Tribblix (illumos, x86_64)",
    "haiku": "Haiku (x86_64; SSH user is 'user', not root)",
    "ubuntu": "Ubuntu Linux (x86_64/aarch64/riscv64/s390x/ppc64le)",
    "openeuler": "openEuler Linux (x86_64/aarch64/riscv64/loongarch64)",
    "blissos": "BlissOS (Android x86_64; root busybox shell over SSH)",
    "hurd": "Debian GNU/Hurd (x86_64/i386)",
    "plan9": "Plan 9 (9front; telnet transport -- exec_in_vm/SSH not available)",
}

# Architectures anyvm.py understands (availability differs per OS/builder).
KNOWN_ARCHES = (
    "x86_64", "aarch64", "riscv64", "sparc64", "powerpc64", "s390x",
    "i386", "loongarch64",
)

# Valid --sync modes (anyvm.py rejects anything else).
SYNC_MODES = ("rsync", "sshfs", "nfs", "sys-nfs", "scp", "9p", "no", "off")

# Guest-side power-off command used for a graceful stop, keyed by OS.
# Fallback for unknown OSes is "poweroff"; plan9 has no SSH at all so the
# QEMU monitor is used instead (see stop_vm).
SHUTDOWN_CMDS: dict[str, str] = {
    "freebsd": "shutdown -p now",
    "ghostbsd": "shutdown -p now",
    "midnightbsd": "shutdown -p now",
    "dragonflybsd": "shutdown -p now",
    "openbsd": "shutdown -p now",
    "netbsd": "shutdown -p now",
    "solaris": "poweroff",
    "omnios": "poweroff",
    "openindiana": "poweroff",
    "tribblix": "poweroff",
    "haiku": "shutdown",
    "ubuntu": "poweroff",
    "openeuler": "poweroff",
    "hurd": "poweroff",
    "blissos": "poweroff",
}

_DEVNULL_KNOWN_HOSTS = "NUL" if os.name == "nt" else "/dev/null"


def _vm_user(os_name: str) -> str:
    """SSH user baked into the images (mirrors anyvm.py)."""
    return "user" if os_name == "haiku" else "root"


@dataclass
class VmInfo:
    """A VM tracked in the manager registry."""

    name: str                    # anyvm image name: <os>-<release>
    os: str
    release: str = ""
    arch: str = ""
    state: str = "unknown"       # running | stopped
    ssh_port: int = 0
    mon_port: int = 0
    user: str = "root"
    key_file: str = ""
    serial_log: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "os": self.os,
            "release": self.release,
            "arch": self.arch,
            "state": self.state,
            "ssh_port": self.ssh_port,
            "mon_port": self.mon_port,
            "user": self.user,
            "key_file": self.key_file,
            "serial_log": self.serial_log,
        }
        if self.state == "running" and self.ssh_port:
            d["ssh_command"] = (
                "ssh -p {} {}@127.0.0.1".format(self.ssh_port, self.user)
            )
        d.update(self.extra)
        return d


def _vendored_anyvm() -> str | None:
    """Return the path to the vendored anyvm.py, or *None* if not bundled."""
    vendor = os.path.join(os.path.dirname(__file__), "vendor", "anyvm.py")
    if os.path.isfile(vendor):
        return vendor
    return None


def _default_data_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".anyvm-mcp")


class VmManager:
    """Boots and manages anyvm guest VMs."""

    def __init__(
        self,
        anyvm_path: str | None = None,
        data_dir: str | None = None,
    ) -> None:
        if anyvm_path:
            self._anyvm = anyvm_path
            self._use_python = anyvm_path.endswith(".py")
        else:
            vendored = _vendored_anyvm()
            if vendored:
                self._anyvm = vendored
                self._use_python = True
            else:
                self._anyvm = shutil.which("anyvm") or "anyvm"
                self._use_python = False
        self._data_dir = os.path.abspath(data_dir or _default_data_dir())
        self._registry_path = os.path.join(self._data_dir, "mcp-registry.json")

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def _load_registry(self) -> dict[str, dict[str, Any]]:
        try:
            with open(self._registry_path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {}

    def _save_registry(self, reg: dict[str, dict[str, Any]]) -> None:
        os.makedirs(self._data_dir, exist_ok=True)
        tmp = self._registry_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(reg, f, indent=2)
        os.replace(tmp, self._registry_path)

    def _entry_to_info(self, name: str, e: dict[str, Any]) -> VmInfo:
        info = VmInfo(
            name=name,
            os=e.get("os", ""),
            release=e.get("release", ""),
            arch=e.get("arch", ""),
            ssh_port=int(e.get("ssh_port", 0)),
            mon_port=int(e.get("mon_port", 0)),
            user=e.get("user", "root"),
            key_file=e.get("key_file", ""),
            serial_log=e.get("serial_log", ""),
        )
        info.state = "running" if self._port_open(info.ssh_port) else "stopped"
        return info

    def _find_entry(self, os_name: str, release: str = "") -> tuple[str, dict[str, Any]]:
        """Locate a registry entry by OS and optional release."""
        if os_name not in SUPPORTED_OS:
            raise AnyvmError(
                "Unsupported OS '{}'. Supported: {}".format(
                    os_name, ", ".join(sorted(SUPPORTED_OS))
                )
            )
        reg = self._load_registry()
        if release:
            name = "{}-{}".format(os_name, release)
            if name not in reg:
                raise AnyvmError(
                    "No VM '{}' in the registry. Start it with start_vm first.".format(name)
                )
            return name, reg[name]
        matches = {n: e for n, e in reg.items() if e.get("os") == os_name}
        if not matches:
            raise AnyvmError(
                "No {} VM in the registry. Start one with start_vm first.".format(os_name)
            )
        if len(matches) > 1:
            raise AnyvmError(
                "Multiple {} VMs found ({}); pass an explicit release.".format(
                    os_name, ", ".join(sorted(matches))
                )
            )
        name = next(iter(matches))
        return name, matches[name]

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _free_port() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
        finally:
            s.close()

    @staticmethod
    def _port_open(port: int, timeout: float = 2.0) -> bool:
        if not port:
            return False
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            return False
        finally:
            s.close()

    def _monitor_cmd(self, port: int, command: str) -> None:
        """Send one command to the QEMU monitor (telnet, localhost)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        try:
            s.connect(("127.0.0.1", port))
            try:
                s.recv(4096)  # banner + prompt
            except socket.timeout:
                pass
            s.sendall((command + "\n").encode("ascii"))
            time.sleep(0.5)
        except OSError as exc:
            raise AnyvmError(
                "QEMU monitor on port {} unreachable: {}".format(port, exc)
            ) from exc
        finally:
            s.close()

    def _anyvm_cmd(self) -> list[str]:
        if self._use_python:
            return [sys.executable, self._anyvm]
        return [self._anyvm]

    def _ssh_cmd(self, e: dict[str, Any]) -> list[str]:
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile={}".format(_DEVNULL_KNOWN_HOSTS),
            "-o", "LogLevel=ERROR",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
        ]
        key_file = e.get("key_file", "")
        if key_file and os.path.exists(key_file):
            cmd.extend(["-i", key_file])
        cmd.extend([
            "-p", str(e.get("ssh_port", 0)),
            "{}@127.0.0.1".format(e.get("user", "root")),
        ])
        return cmd

    def _discover_artifacts(self, os_name: str) -> tuple[str, str, str]:
        """Find the freshest ``<os>-<release>.serial.log`` under the data dir.

        Returns (vm_name, serial_log_path, key_file_path). anyvm writes the
        serial log and the ``<vm>-host.id_rsa`` key side by side in
        ``<data-dir>/<os>/v<builder>/``.
        """
        pattern = os.path.join(
            self._data_dir, os_name, "v*", "{}-*.serial.log".format(os_name)
        )
        candidates = glob.glob(pattern)
        if not candidates:
            raise AnyvmError(
                "Boot reported success but no serial log matches {}".format(pattern)
            )
        latest = max(candidates, key=os.path.getmtime)
        base = os.path.basename(latest)
        vm_name = base[: -len(".serial.log")]
        key_file = os.path.join(os.path.dirname(latest), vm_name + "-host.id_rsa")
        return vm_name, latest, key_file

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_supported_os(self) -> list[dict[str, str]]:
        """Describe every OS anyvm can boot."""
        out = []
        for name in sorted(SUPPORTED_OS):
            out.append({
                "os": name,
                "notes": SUPPORTED_OS[name],
                "ssh_user": _vm_user(name),
            })
        return out

    def start_vm(
        self,
        os_name: str,
        *,
        release: str = "",
        arch: str = "",
        mem_mb: int = 0,
        cpus: int = 0,
        ports: list[str] | None = None,
        volumes: list[str] | None = None,
        sync: str = "",
        snapshot_mode: bool = False,
        boot_timeout_sec: int = 1800,
        extra_args: list[str] | None = None,
    ) -> VmInfo:
        """Boot a guest VM in the background and register it.

        The first boot of an OS/release downloads the image from the
        matching anyvm builder release (can be several hundred MB), so
        *boot_timeout_sec* must cover download + boot.
        """
        if os_name not in SUPPORTED_OS:
            raise AnyvmError(
                "Unsupported OS '{}'. Supported: {}".format(
                    os_name, ", ".join(sorted(SUPPORTED_OS))
                )
            )
        if arch and arch not in KNOWN_ARCHES:
            raise AnyvmError(
                "Unknown arch '{}'. Known: {}".format(arch, ", ".join(KNOWN_ARCHES))
            )
        if sync and sync not in SYNC_MODES:
            raise AnyvmError(
                "Invalid sync mode '{}'. Supported: {}".format(
                    sync, ", ".join(SYNC_MODES)
                )
            )
        if sync and sync not in ("no", "off") and not volumes:
            raise AnyvmError("sync='{}' requires at least one volume mapping".format(sync))

        ssh_port = self._free_port()
        mon_port = self._free_port()

        cmd = self._anyvm_cmd() + [
            "--os", os_name,
            "--data-dir", self._data_dir,
            "--ssh-port", str(ssh_port),
            "--mon", str(mon_port),
            "--detach",
            "--remote-vnc", "no",
        ]
        if release:
            cmd.extend(["--release", release])
        if arch:
            cmd.extend(["--arch", arch])
        if mem_mb:
            cmd.extend(["--mem", str(mem_mb)])
        if cpus:
            cmd.extend(["--cpu", str(cpus)])
        for p in ports or []:
            cmd.extend(["-p", p])
        for v in volumes or []:
            cmd.extend(["-v", v])
        if sync:
            cmd.extend(["--sync", sync])
        if snapshot_mode:
            cmd.append("--snapshot")
        if extra_args:
            cmd.extend(extra_args)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=boot_timeout_sec,
            )
        except FileNotFoundError as exc:
            raise AnyvmError(
                "anyvm launcher not found at '{}'.".format(self._anyvm)
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AnyvmError(
                "anyvm did not finish within {}s (image download on first "
                "boot can be slow; raise boot_timeout_sec).".format(boot_timeout_sec)
            ) from exc

        if result.returncode != 0:
            tail = "\n".join(
                (result.stderr or result.stdout or "").strip().splitlines()[-30:]
            )
            raise AnyvmError(
                "anyvm boot failed (exit {}):\n{}".format(result.returncode, tail)
            )

        vm_name, serial_log, key_file = self._discover_artifacts(os_name)
        discovered_release = vm_name[len(os_name) + 1:] if vm_name.startswith(os_name + "-") else release

        reg = self._load_registry()
        reg[vm_name] = {
            "os": os_name,
            "release": discovered_release,
            "arch": arch,
            "ssh_port": ssh_port,
            "mon_port": mon_port,
            "user": _vm_user(os_name),
            "key_file": key_file,
            "serial_log": serial_log,
            "started_at": int(time.time()),
        }
        self._save_registry(reg)
        return self._entry_to_info(vm_name, reg[vm_name])

    def list_vms(self) -> list[VmInfo]:
        """All registry VMs with a live running/stopped probe."""
        reg = self._load_registry()
        return [self._entry_to_info(n, e) for n, e in sorted(reg.items())]

    def vm_info(self, os_name: str, release: str = "") -> VmInfo:
        name, e = self._find_entry(os_name, release)
        return self._entry_to_info(name, e)

    def exec_in_vm(
        self,
        os_name: str,
        command: str,
        *,
        release: str = "",
        timeout_sec: int = 120,
    ) -> str:
        """Run *command* over SSH in a running VM; returns stdout+stderr."""
        if os_name == "plan9":
            raise AnyvmError(
                "plan9 guests have no SSH (telnet transport); exec_in_vm is "
                "not supported for plan9."
            )
        name, e = self._find_entry(os_name, release)
        if not self._port_open(int(e.get("ssh_port", 0))):
            raise AnyvmError(
                "VM '{}' is not running (SSH port {} closed).".format(
                    name, e.get("ssh_port")
                )
            )
        cmd = self._ssh_cmd(e) + [command]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_sec
            )
        except subprocess.TimeoutExpired as exc:
            raise AnyvmError(
                "Command timed out after {}s in VM '{}'.".format(timeout_sec, name)
            ) from exc
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            out += "\n[exit code {}]".format(result.returncode)
        return out.strip()

    def stop_vm(
        self,
        os_name: str,
        *,
        release: str = "",
        force: bool = False,
        wait_sec: int = 90,
    ) -> str:
        """Stop a running VM.

        Graceful path: OS-appropriate shutdown command over SSH, falling
        back to an ACPI powerdown via the QEMU monitor. ``force=True`` sends
        ``quit`` to the QEMU monitor (hard kill, like pulling the plug).
        """
        name, e = self._find_entry(os_name, release)
        ssh_port = int(e.get("ssh_port", 0))
        mon_port = int(e.get("mon_port", 0))

        if not self._port_open(ssh_port) and not self._port_open(mon_port):
            self._forget(name)
            return "VM '{}' was already stopped; registry entry removed.".format(name)

        how = ""
        if force:
            self._monitor_cmd(mon_port, "quit")
            how = "QEMU monitor 'quit' (hard stop)"
        else:
            sent = False
            if os_name != "plan9" and self._port_open(ssh_port):
                shutdown_cmd = SHUTDOWN_CMDS.get(os_name, "poweroff")
                try:
                    subprocess.run(
                        self._ssh_cmd(e) + [shutdown_cmd],
                        capture_output=True, text=True, timeout=30,
                    )
                    sent = True
                    how = "guest '{}' over SSH".format(shutdown_cmd)
                except (subprocess.TimeoutExpired, OSError):
                    sent = False
            if not sent:
                self._monitor_cmd(mon_port, "system_powerdown")
                how = "ACPI powerdown via QEMU monitor"

        deadline = time.time() + wait_sec
        while time.time() < deadline:
            if not self._port_open(ssh_port) and not self._port_open(mon_port):
                self._forget(name)
                return "VM '{}' stopped ({}).".format(name, how)
            time.sleep(2)

        if force:
            raise AnyvmError(
                "VM '{}' still up {}s after monitor quit.".format(name, wait_sec)
            )
        self._monitor_cmd(mon_port, "quit")
        time.sleep(2)
        if not self._port_open(ssh_port) and not self._port_open(mon_port):
            self._forget(name)
            return (
                "VM '{}' did not shut down gracefully within {}s; "
                "hard-stopped via QEMU monitor.".format(name, wait_sec)
            )
        raise AnyvmError("Failed to stop VM '{}'.".format(name))

    def _forget(self, name: str) -> None:
        reg = self._load_registry()
        if name in reg:
            del reg[name]
            self._save_registry(reg)

    def console_output(self, os_name: str, *, release: str = "", lines: int = 100) -> str:
        """Tail the VM's serial console log."""
        name, e = self._find_entry(os_name, release)
        path = e.get("serial_log", "")
        if not path or not os.path.exists(path):
            raise AnyvmError(
                "No serial log found for VM '{}' (expected {}).".format(name, path)
            )
        with open(path, "rb") as f:
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-max(1, lines):])
