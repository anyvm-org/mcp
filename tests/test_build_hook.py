"""Tests for the build hook that downloads anyvm.py during build."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("hatchling", reason="hatchling not installed")

from build_hook import AnyvmDownloadHook  # noqa: E402


@pytest.fixture()
def hook(tmp_path):
    """Create an AnyvmDownloadHook with a temp directory as the project root."""
    src_dir = tmp_path / "src" / "anyvm_mcp"
    src_dir.mkdir(parents=True)

    mock_hook = MagicMock(spec=AnyvmDownloadHook)
    mock_hook.config = {"anyvm-version": "v0.5.2"}
    mock_hook.initialize = AnyvmDownloadHook.initialize.__get__(mock_hook)
    mock_hook.finalize = AnyvmDownloadHook.finalize.__get__(mock_hook)

    return mock_hook, tmp_path


class TestAnyvmDownloadHook:
    def test_initialize_downloads_and_creates_vendor(self, hook, tmp_path):
        mock_hook, _ = hook
        vendor_dir = tmp_path / "src" / "anyvm_mcp" / "vendor"

        with patch("build_hook.os.path.dirname", return_value=str(tmp_path)):
            with patch("build_hook.urllib.request.urlretrieve") as mock_dl:
                mock_hook.initialize("0.0.1", {})

        assert vendor_dir.is_dir()
        assert (vendor_dir / "__init__.py").exists()
        mock_dl.assert_called_once()
        call_url = mock_dl.call_args[0][0]
        assert "v0.5.2" in call_url
        assert call_url.endswith("/anyvm.py")

    def test_initialize_uses_configured_version(self, hook, tmp_path):
        mock_hook, _ = hook
        mock_hook.config = {"anyvm-version": "v0.1.0"}

        with patch("build_hook.os.path.dirname", return_value=str(tmp_path)):
            with patch("build_hook.urllib.request.urlretrieve") as mock_dl:
                mock_hook.initialize("0.0.1", {})

        call_url = mock_dl.call_args[0][0]
        assert "v0.1.0" in call_url

    def test_initialize_default_version(self, hook, tmp_path):
        mock_hook, _ = hook
        mock_hook.config = {}

        with patch("build_hook.os.path.dirname", return_value=str(tmp_path)):
            with patch("build_hook.urllib.request.urlretrieve") as mock_dl:
                mock_hook.initialize("0.0.1", {})

        call_url = mock_dl.call_args[0][0]
        assert "v0.5.2" in call_url

    def test_finalize_removes_vendor_dir(self, hook, tmp_path):
        mock_hook, _ = hook
        vendor_dir = tmp_path / "src" / "anyvm_mcp" / "vendor"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "anyvm.py").write_text("# fake")

        with patch("build_hook.os.path.dirname", return_value=str(tmp_path)):
            mock_hook.finalize("0.0.1", {}, "/fake/artifact.whl")

        assert not vendor_dir.exists()

    def test_finalize_noop_when_no_vendor(self, hook, tmp_path):
        mock_hook, _ = hook

        with patch("build_hook.os.path.dirname", return_value=str(tmp_path)):
            mock_hook.finalize("0.0.1", {}, "/fake/artifact.whl")
