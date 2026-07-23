"""Tests for VmManager -- anyvm launcher invocation is mocked, the registry
and artifact discovery run against a real temp data dir."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from anyvm_mcp.vm_manager import (
    KNOWN_ARCHES,
    SHUTDOWN_CMDS,
    SUPPORTED_OS,
    SYNC_MODES,
    AnyvmError,
    VmInfo,
    VmManager,
    _vendored_anyvm,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def mgr(tmp_path) -> VmManager:
    """VmManager with a fake anyvm.py path and a temp data dir."""
    return VmManager(anyvm_path="/opt/anyvm/anyvm.py", data_dir=str(tmp_path))


def _fake_artifacts(tmp_path, vm_name="freebsd-14.3", builder="v2.2.5"):
    """Create the on-disk artifacts anyvm leaves after a detached boot."""
    os_name = vm_name.split("-")[0]
    out_dir = tmp_path / os_name / builder
    out_dir.mkdir(parents=True, exist_ok=True)
    serial = out_dir / "{}.serial.log".format(vm_name)
    serial.write_text("login: \n")
    key = out_dir / "{}-host.id_rsa".format(vm_name)
    key.write_text("fake-key")
    return serial, key


def _completed(rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def _registry_entry(tmp_path, vm_name="freebsd-14.3", ssh_port=10022, mon_port=10023):
    serial, key = _fake_artifacts(tmp_path, vm_name)
    os_name = vm_name.split("-")[0]
    return vm_name, {
        "os": os_name,
        "release": vm_name[len(os_name) + 1:],
        "arch": "",
        "ssh_port": ssh_port,
        "mon_port": mon_port,
        "user": "user" if os_name == "haiku" else "root",
        "key_file": str(key),
        "serial_log": str(serial),
        "started_at": 0,
    }


def _seed_registry(mgr_obj, tmp_path, vm_name="freebsd-14.3", **kw):
    name, entry = _registry_entry(tmp_path, vm_name, **kw)
    mgr_obj._save_registry({name: entry})
    return name, entry


# ---------------------------------------------------------------------------
# Static tables
# ---------------------------------------------------------------------------


class TestStaticTables:
    def test_supported_os_matches_anyvm(self):
        expected = {
            "freebsd", "openbsd", "netbsd", "dragonflybsd", "ghostbsd",
            "midnightbsd", "solaris", "omnios", "openindiana", "tribblix",
            "haiku", "ubuntu", "openeuler", "blissos", "hurd", "plan9",
        }
        assert set(SUPPORTED_OS) == expected

    def test_sync_modes(self):
        assert set(SYNC_MODES) == {
            "rsync", "sshfs", "nfs", "sys-nfs", "scp", "9p", "no", "off"
        }

    def test_shutdown_cmds_cover_ssh_os(self):
        # Every OS except plan9 (no SSH) has a shutdown command.
        assert set(SHUTDOWN_CMDS) == set(SUPPORTED_OS) - {"plan9"}

    def test_list_supported_os(self, mgr):
        rows = mgr.list_supported_os()
        assert len(rows) == len(SUPPORTED_OS)
        by_os = {r["os"]: r for r in rows}
        assert by_os["haiku"]["ssh_user"] == "user"
        assert by_os["freebsd"]["ssh_user"] == "root"


# ---------------------------------------------------------------------------
# start_vm
# ---------------------------------------------------------------------------


class TestStartVm:
    def test_rejects_unsupported_os(self, mgr):
        with pytest.raises(AnyvmError, match="Unsupported OS"):
            mgr.start_vm("smartos")

    def test_rejects_bad_arch(self, mgr):
        with pytest.raises(AnyvmError, match="Unknown arch"):
            mgr.start_vm("freebsd", arch="mips")

    def test_rejects_bad_sync(self, mgr):
        with pytest.raises(AnyvmError, match="Invalid sync mode"):
            mgr.start_vm("freebsd", sync="ftp", volumes=["/a:/b"])

    def test_rejects_sync_without_volumes(self, mgr):
        with pytest.raises(AnyvmError, match="requires at least one volume"):
            mgr.start_vm("freebsd", sync="rsync")

    def test_builds_launcher_command(self, mgr, tmp_path):
        _fake_artifacts(tmp_path, "freebsd-14.3")
        with patch("anyvm_mcp.vm_manager.subprocess.run", return_value=_completed()) as run:
            with patch.object(VmManager, "_free_port", side_effect=[10022, 10023]):
                with patch.object(VmManager, "_port_open", return_value=True):
                    info = mgr.start_vm(
                        "freebsd",
                        release="14.3",
                        arch="x86_64",
                        mem_mb=2048,
                        cpus=2,
                        ports=["8080:80"],
                        volumes=["/tmp/x:/x"],
                        sync="rsync",
                    )

        cmd = run.call_args[0][0]
        assert cmd[0] == sys.executable
        assert cmd[1] == "/opt/anyvm/anyvm.py"
        joined = " ".join(cmd)
        assert "--os freebsd" in joined
        assert "--release 14.3" in joined
        assert "--arch x86_64" in joined
        assert "--mem 2048" in joined
        assert "--cpu 2" in joined
        assert "--ssh-port 10022" in joined
        assert "--mon 10023" in joined
        assert "--detach" in joined
        assert "--remote-vnc no" in joined
        assert "-p 8080:80" in joined
        assert "-v /tmp/x:/x" in joined
        assert "--sync rsync" in joined
        # No fictional subcommands.
        assert "create" not in cmd
        assert "--format" not in cmd

        assert info.name == "freebsd-14.3"
        assert info.release == "14.3"
        assert info.state == "running"
        assert info.ssh_port == 10022
        assert info.user == "root"

    def test_registers_vm(self, mgr, tmp_path):
        _fake_artifacts(tmp_path, "openbsd-7.9")
        with patch("anyvm_mcp.vm_manager.subprocess.run", return_value=_completed()):
            with patch.object(VmManager, "_free_port", side_effect=[11022, 11023]):
                with patch.object(VmManager, "_port_open", return_value=True):
                    mgr.start_vm("openbsd", release="7.9")

        reg = json.loads((tmp_path / "mcp-registry.json").read_text())
        assert "openbsd-7.9" in reg
        assert reg["openbsd-7.9"]["ssh_port"] == 11022
        assert reg["openbsd-7.9"]["mon_port"] == 11023

    def test_discovers_release_when_omitted(self, mgr, tmp_path):
        # anyvm auto-picks the release; the serial log name reveals it.
        _fake_artifacts(tmp_path, "netbsd-10.1")
        with patch("anyvm_mcp.vm_manager.subprocess.run", return_value=_completed()):
            with patch.object(VmManager, "_free_port", side_effect=[12022, 12023]):
                with patch.object(VmManager, "_port_open", return_value=True):
                    info = mgr.start_vm("netbsd")
        assert info.name == "netbsd-10.1"
        assert info.release == "10.1"

    def test_boot_failure_raises_with_output_tail(self, mgr, tmp_path):
        with patch(
            "anyvm_mcp.vm_manager.subprocess.run",
            return_value=_completed(rc=1, stderr="Error: boot timeout"),
        ):
            with pytest.raises(AnyvmError, match="boot timeout"):
                mgr.start_vm("freebsd", release="14.3")

    def test_launcher_missing(self, mgr):
        with patch(
            "anyvm_mcp.vm_manager.subprocess.run", side_effect=FileNotFoundError()
        ):
            with pytest.raises(AnyvmError, match="not found"):
                mgr.start_vm("freebsd", release="14.3")

    def test_haiku_user(self, mgr, tmp_path):
        _fake_artifacts(tmp_path, "haiku-r1beta5")
        with patch("anyvm_mcp.vm_manager.subprocess.run", return_value=_completed()):
            with patch.object(VmManager, "_free_port", side_effect=[13022, 13023]):
                with patch.object(VmManager, "_port_open", return_value=True):
                    info = mgr.start_vm("haiku")
        assert info.user == "user"


# ---------------------------------------------------------------------------
# list / info
# ---------------------------------------------------------------------------


class TestListAndInfo:
    def test_list_empty(self, mgr):
        assert mgr.list_vms() == []

    def test_list_probes_state(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path)
        with patch.object(VmManager, "_port_open", return_value=False):
            vms = mgr.list_vms()
        assert len(vms) == 1
        assert vms[0].state == "stopped"

    def test_info_by_os_only(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path)
        with patch.object(VmManager, "_port_open", return_value=True):
            info = mgr.vm_info("freebsd")
        assert info.name == "freebsd-14.3"
        assert info.state == "running"
        assert "ssh_command" in info.to_dict()

    def test_info_ambiguous_needs_release(self, mgr, tmp_path):
        n1, e1 = _registry_entry(tmp_path, "freebsd-14.3")
        n2, e2 = _registry_entry(tmp_path, "freebsd-13.5", ssh_port=10122, mon_port=10123)
        mgr._save_registry({n1: e1, n2: e2})
        with pytest.raises(AnyvmError, match="explicit release"):
            mgr.vm_info("freebsd")
        with patch.object(VmManager, "_port_open", return_value=True):
            info = mgr.vm_info("freebsd", "13.5")
        assert info.name == "freebsd-13.5"

    def test_info_unknown_vm(self, mgr):
        with pytest.raises(AnyvmError, match="start_vm first"):
            mgr.vm_info("openbsd")

    def test_info_unsupported_os(self, mgr):
        with pytest.raises(AnyvmError, match="Unsupported OS"):
            mgr.vm_info("windows")


# ---------------------------------------------------------------------------
# exec_in_vm
# ---------------------------------------------------------------------------


class TestExecInVm:
    def test_builds_ssh_command(self, mgr, tmp_path):
        name, entry = _seed_registry(mgr, tmp_path)
        with patch.object(VmManager, "_port_open", return_value=True):
            with patch(
                "anyvm_mcp.vm_manager.subprocess.run",
                return_value=_completed(stdout="FreeBSD 14.3\n"),
            ) as run:
                out = mgr.exec_in_vm("freebsd", "uname -sr")

        cmd = run.call_args[0][0]
        assert cmd[0] == "ssh"
        joined = " ".join(cmd)
        assert "-p 10022" in joined
        assert "root@127.0.0.1" in joined
        assert "-i {}".format(entry["key_file"]) in joined
        assert "BatchMode=yes" in joined
        assert cmd[-1] == "uname -sr"
        assert out == "FreeBSD 14.3"

    def test_nonzero_exit_appended(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path)
        with patch.object(VmManager, "_port_open", return_value=True):
            with patch(
                "anyvm_mcp.vm_manager.subprocess.run",
                return_value=_completed(rc=127, stderr="sh: nope: not found\n"),
            ):
                out = mgr.exec_in_vm("freebsd", "nope")
        assert "not found" in out
        assert "[exit code 127]" in out

    def test_requires_running_vm(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path)
        with patch.object(VmManager, "_port_open", return_value=False):
            with pytest.raises(AnyvmError, match="not running"):
                mgr.exec_in_vm("freebsd", "uname")

    def test_plan9_rejected(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path, "plan9-9front")
        with pytest.raises(AnyvmError, match="no SSH"):
            mgr.exec_in_vm("plan9", "ls")

    def test_timeout(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path)
        with patch.object(VmManager, "_port_open", return_value=True):
            with patch(
                "anyvm_mcp.vm_manager.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=5),
            ):
                with pytest.raises(AnyvmError, match="timed out"):
                    mgr.exec_in_vm("freebsd", "sleep 999", timeout_sec=5)


# ---------------------------------------------------------------------------
# stop_vm
# ---------------------------------------------------------------------------


class TestStopVm:
    def test_graceful_ssh_shutdown(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path)
        # Ports open before the shutdown command, closed afterwards.
        port_alive = {"alive": True}

        def fake_port_open(self, port, timeout=2.0):
            return port_alive["alive"]

        def fake_run(cmd, **kw):
            port_alive["alive"] = False
            return _completed()

        with patch.object(VmManager, "_port_open", fake_port_open):
            with patch("anyvm_mcp.vm_manager.subprocess.run", side_effect=fake_run) as run:
                msg = mgr.stop_vm("freebsd")

        assert "shutdown -p now" in run.call_args[0][0]
        assert "stopped" in msg
        assert mgr._load_registry() == {}

    def test_force_uses_monitor_quit(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path)
        port_alive = {"alive": True}

        def fake_port_open(self, port, timeout=2.0):
            return port_alive["alive"]

        def fake_monitor(self, port, command):
            assert command == "quit"
            port_alive["alive"] = False

        with patch.object(VmManager, "_port_open", fake_port_open):
            with patch.object(VmManager, "_monitor_cmd", fake_monitor):
                msg = mgr.stop_vm("freebsd", force=True)
        assert "stopped" in msg

    def test_already_stopped_prunes_registry(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path)
        with patch.object(VmManager, "_port_open", return_value=False):
            msg = mgr.stop_vm("freebsd")
        assert "already stopped" in msg
        assert mgr._load_registry() == {}

    def test_plan9_uses_monitor(self, mgr, tmp_path):
        _seed_registry(mgr, tmp_path, "plan9-9front")
        port_alive = {"alive": True}
        sent = []

        def fake_port_open(self, port, timeout=2.0):
            return port_alive["alive"]

        def fake_monitor(self, port, command):
            sent.append(command)
            port_alive["alive"] = False

        with patch.object(VmManager, "_port_open", fake_port_open):
            with patch.object(VmManager, "_monitor_cmd", fake_monitor):
                mgr.stop_vm("plan9")
        assert sent == ["system_powerdown"]


# ---------------------------------------------------------------------------
# console_output
# ---------------------------------------------------------------------------


class TestConsoleOutput:
    def test_tails_serial_log(self, mgr, tmp_path):
        name, entry = _seed_registry(mgr, tmp_path)
        with open(entry["serial_log"], "w") as f:
            f.write("\n".join("line{}".format(i) for i in range(200)))
        out = mgr.console_output("freebsd", lines=5)
        assert out.splitlines() == ["line195", "line196", "line197", "line198", "line199"]

    def test_missing_log(self, mgr, tmp_path):
        name, entry = _seed_registry(mgr, tmp_path)
        os.remove(entry["serial_log"])
        with pytest.raises(AnyvmError, match="No serial log"):
            mgr.console_output("freebsd")


# ---------------------------------------------------------------------------
# Launcher resolution
# ---------------------------------------------------------------------------


class TestLauncherResolution:
    def test_explicit_py_path_uses_python(self, tmp_path):
        m = VmManager(anyvm_path="/x/anyvm.py", data_dir=str(tmp_path))
        assert m._anyvm_cmd() == [sys.executable, "/x/anyvm.py"]

    def test_explicit_binary_path(self, tmp_path):
        m = VmManager(anyvm_path="/usr/local/bin/anyvm", data_dir=str(tmp_path))
        assert m._anyvm_cmd() == ["/usr/local/bin/anyvm"]

    def test_vendored_lookup(self, tmp_path):
        with patch("anyvm_mcp.vm_manager._vendored_anyvm", return_value="/v/anyvm.py"):
            m = VmManager(data_dir=str(tmp_path))
        assert m._anyvm_cmd() == [sys.executable, "/v/anyvm.py"]

    def test_vendored_helper_returns_none_when_absent(self):
        # The source tree has no vendor dir (it is created at build time).
        assert _vendored_anyvm() is None
