"""Tests for VmManager — uses unittest.mock to stub the anyvm CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from anyvm_mcp.vm_manager import AnyvmError, SnapshotInfo, VmInfo, VmManager, _vendored_anyvm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mgr() -> VmManager:
    """Return a VmManager pointing at a fake 'anyvm' path."""
    return VmManager(anyvm_path="/usr/local/bin/anyvm")


# ---------------------------------------------------------------------------
# VmInfo / SnapshotInfo dataclass tests
# ---------------------------------------------------------------------------


class TestVmInfo:
    def test_from_dict_minimal(self):
        info = VmInfo.from_dict({"name": "my-vm", "state": "running"})
        assert info.name == "my-vm"
        assert info.state == "running"
        assert info.cpus == 1
        assert info.memory_mb == 512
        assert info.ip == ""

    def test_from_dict_full(self):
        data = {
            "name": "bsd-box",
            "state": "stopped",
            "os": "freebsd-14",
            "cpus": 4,
            "memory": 2048,
            "ip": "192.168.1.10",
            "disk": "20G",
        }
        info = VmInfo.from_dict(data)
        assert info.name == "bsd-box"
        assert info.os == "freebsd-14"
        assert info.cpus == 4
        assert info.memory_mb == 2048
        assert info.ip == "192.168.1.10"
        assert info.extra["disk"] == "20G"

    def test_to_dict_round_trip(self):
        info = VmInfo(name="vm1", state="running", os="openbsd-7", cpus=2, memory_mb=1024)
        d = info.to_dict()
        assert d["name"] == "vm1"
        assert d["state"] == "running"
        assert d["os"] == "openbsd-7"
        assert d["cpus"] == 2
        assert d["memory_mb"] == 1024


class TestSnapshotInfo:
    def test_from_dict(self):
        snap = SnapshotInfo.from_dict(
            "vm1", {"name": "snap1", "created": "2024-01-01", "description": "test"}
        )
        assert snap.name == "snap1"
        assert snap.vm_name == "vm1"
        assert snap.created == "2024-01-01"
        assert snap.description == "test"

    def test_to_dict(self):
        snap = SnapshotInfo(name="s1", vm_name="vm1", created="2024-01-01")
        d = snap.to_dict()
        assert d["name"] == "s1"
        assert d["vm_name"] == "vm1"


# ---------------------------------------------------------------------------
# VmManager._run tests
# ---------------------------------------------------------------------------


def _make_completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


class TestVmManagerRun:
    def test_run_success(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("hello")) as mock_run:
            output = mgr._run("list")
        mock_run.assert_called_once()
        assert output == "hello"

    def test_run_failure_raises_anyvm_error(self, mgr: VmManager):
        with patch(
            "subprocess.run",
            return_value=_make_completed("", returncode=1, stderr="VM not found"),
        ):
            with pytest.raises(AnyvmError, match="VM not found"):
                mgr._run("start", "missing-vm")

    def test_run_binary_not_found(self, mgr: VmManager):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(AnyvmError, match="anyvm CLI not found"):
                mgr._run("list")

    def test_run_json_valid(self, mgr: VmManager):
        payload = json.dumps([{"name": "vm1", "state": "running"}])
        with patch("subprocess.run", return_value=_make_completed(payload)):
            result = mgr._run_json("list")
        assert result[0]["name"] == "vm1"

    def test_run_json_invalid(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("not-json")):
            with pytest.raises(AnyvmError, match="non-JSON"):
                mgr._run_json("list")


# ---------------------------------------------------------------------------
# VmManager high-level method tests
# ---------------------------------------------------------------------------


class TestListVms:
    def test_returns_vm_infos(self, mgr: VmManager):
        payload = json.dumps([
            {"name": "vm1", "state": "running", "os": "freebsd-14"},
            {"name": "vm2", "state": "stopped", "os": "openbsd-7"},
        ])
        with patch("subprocess.run", return_value=_make_completed(payload)):
            vms = mgr.list_vms()
        assert len(vms) == 2
        assert vms[0].name == "vm1"
        assert vms[1].state == "stopped"

    def test_handles_wrapped_list(self, mgr: VmManager):
        payload = json.dumps({"vms": [{"name": "vm3", "state": "running"}]})
        with patch("subprocess.run", return_value=_make_completed(payload)):
            vms = mgr.list_vms()
        assert vms[0].name == "vm3"


class TestCreateVm:
    def test_create_calls_cli_then_info(self, mgr: VmManager):
        info_payload = json.dumps(
            {"name": "new-vm", "state": "stopped", "os": "freebsd-14"}
        )
        # create → empty stdout; info → JSON
        responses = [
            _make_completed(""),
            _make_completed(info_payload),
        ]
        with patch("subprocess.run", side_effect=responses):
            info = mgr.create_vm("new-vm", "freebsd-14", cpus=2, memory_mb=1024, disk_gb=40)
        assert info.name == "new-vm"
        assert info.os == "freebsd-14"

    def test_create_propagates_error(self, mgr: VmManager):
        with patch(
            "subprocess.run",
            return_value=_make_completed("", returncode=1, stderr="name already in use"),
        ):
            with pytest.raises(AnyvmError, match="name already in use"):
                mgr.create_vm("existing", "freebsd-14")


class TestStartStopDestroy:
    def test_start_vm(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("VM started")):
            result = mgr.start_vm("my-vm")
        assert "started" in result.lower()

    def test_stop_vm(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("VM stopped")):
            result = mgr.stop_vm("my-vm")
        assert "stopped" in result.lower()

    def test_stop_vm_force(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("VM stopped")) as mock_run:
            mgr.stop_vm("my-vm", force=True)
        call_args = mock_run.call_args[0][0]
        assert "--force" in call_args

    def test_destroy_vm_passes_yes(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("destroyed")) as mock_run:
            mgr.destroy_vm("old-vm")
        call_args = mock_run.call_args[0][0]
        assert "--yes" in call_args


class TestExecInVm:
    def test_exec_returns_output(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("FreeBSD 14.0")):
            out = mgr.exec_in_vm("my-vm", "uname -r")
        assert "FreeBSD" in out


class TestConsoleOutput:
    def test_console_returns_lines(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("boot log line")) as mock_run:
            out = mgr.console_output("my-vm", lines=50)
        call_args = mock_run.call_args[0][0]
        assert "--lines" in call_args
        assert "50" in call_args
        assert "boot log line" in out


class TestSnapshots:
    def test_list_snapshots(self, mgr: VmManager):
        payload = json.dumps([{"name": "snap1", "created": "2024-01-01"}])
        with patch("subprocess.run", return_value=_make_completed(payload)):
            snaps = mgr.list_snapshots("my-vm")
        assert snaps[0].name == "snap1"

    def test_list_snapshots_wrapped(self, mgr: VmManager):
        payload = json.dumps({"snapshots": [{"name": "s1", "created": "2024-06-01"}]})
        with patch("subprocess.run", return_value=_make_completed(payload)):
            snaps = mgr.list_snapshots("my-vm")
        assert snaps[0].name == "s1"

    def test_create_snapshot(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("")):
            snap = mgr.create_snapshot("my-vm", "snap1", description="before upgrade")
        assert snap.name == "snap1"
        assert snap.vm_name == "my-vm"

    def test_restore_snapshot(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("restored")):
            result = mgr.restore_snapshot("my-vm", "snap1")
        assert "restored" in result

    def test_delete_snapshot(self, mgr: VmManager):
        with patch("subprocess.run", return_value=_make_completed("deleted")):
            result = mgr.delete_snapshot("my-vm", "snap1")
        assert "deleted" in result


class TestNetworkInfo:
    def test_network_info_dict(self, mgr: VmManager):
        payload = json.dumps({"ip": "10.0.0.5", "mac": "aa:bb:cc:dd:ee:ff"})
        with patch("subprocess.run", return_value=_make_completed(payload)):
            info = mgr.network_info("my-vm")
        assert info["ip"] == "10.0.0.5"

    def test_network_info_list_wrapped(self, mgr: VmManager):
        payload = json.dumps([{"interface": "vtnet0", "ip": "10.0.0.5"}])
        with patch("subprocess.run", return_value=_make_completed(payload)):
            info = mgr.network_info("my-vm")
        assert "interfaces" in info


# ---------------------------------------------------------------------------
# Vendored anyvm.py tests
# ---------------------------------------------------------------------------


class TestVendoredAnyvm:
    def test_vendored_anyvm_returns_path_when_file_exists(self, tmp_path):
        vendor_dir = tmp_path / "vendor"
        vendor_dir.mkdir()
        anyvm_file = vendor_dir / "anyvm.py"
        anyvm_file.write_text("# fake anyvm")

        with patch("anyvm_mcp.vm_manager.os.path.dirname", return_value=str(tmp_path)):
            result = _vendored_anyvm()
        assert result is not None
        assert result.endswith("anyvm.py")

    def test_vendored_anyvm_returns_none_when_missing(self, tmp_path):
        with patch("anyvm_mcp.vm_manager.os.path.dirname", return_value=str(tmp_path)):
            result = _vendored_anyvm()
        assert result is None


class TestVmManagerInit:
    def test_explicit_path_no_python(self):
        mgr = VmManager(anyvm_path="/custom/anyvm")
        assert mgr._anyvm == "/custom/anyvm"
        assert mgr._use_python is False

    def test_vendored_uses_python(self, tmp_path):
        vendor_dir = tmp_path / "vendor"
        vendor_dir.mkdir()
        (vendor_dir / "anyvm.py").write_text("# fake")

        with patch("anyvm_mcp.vm_manager._vendored_anyvm", return_value=str(vendor_dir / "anyvm.py")):
            mgr = VmManager()
        assert mgr._anyvm == str(vendor_dir / "anyvm.py")
        assert mgr._use_python is True

    def test_fallback_to_path(self):
        with patch("anyvm_mcp.vm_manager._vendored_anyvm", return_value=None):
            with patch("shutil.which", return_value="/usr/bin/anyvm"):
                mgr = VmManager()
        assert mgr._anyvm == "/usr/bin/anyvm"
        assert mgr._use_python is False

    def test_fallback_to_default(self):
        with patch("anyvm_mcp.vm_manager._vendored_anyvm", return_value=None):
            with patch("shutil.which", return_value=None):
                mgr = VmManager()
        assert mgr._anyvm == "anyvm"
        assert mgr._use_python is False


class TestRunWithVendored:
    def test_run_uses_sys_executable_for_vendored(self):
        with patch("anyvm_mcp.vm_manager._vendored_anyvm", return_value="/pkg/vendor/anyvm.py"):
            mgr = VmManager()

        with patch("subprocess.run", return_value=_make_completed("ok")) as mock_run:
            mgr._run("list")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == sys.executable
        assert cmd[1] == "/pkg/vendor/anyvm.py"
        assert cmd[2] == "list"

    def test_run_direct_for_explicit_path(self):
        mgr = VmManager(anyvm_path="/usr/local/bin/anyvm")

        with patch("subprocess.run", return_value=_make_completed("ok")) as mock_run:
            mgr._run("list")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/anyvm"
        assert cmd[1] == "list"
