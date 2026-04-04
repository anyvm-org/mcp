"""Hatchling build hook that downloads a pinned version of anyvm.py into the package."""

from __future__ import annotations

import os
import shutil
import urllib.request

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class AnyvmDownloadHook(BuildHookInterface):
    PLUGIN_NAME = "anyvm-download"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        anyvm_version = self.config.get("anyvm-version", "v0.3.2")
        url = f"https://github.com/anyvm-org/anyvm/raw/{anyvm_version}/anyvm.py"

        vendor_dir = os.path.join(
            os.path.dirname(__file__), "src", "anyvm_mcp", "vendor"
        )
        os.makedirs(vendor_dir, exist_ok=True)

        dest = os.path.join(vendor_dir, "anyvm.py")
        print(f"Downloading anyvm {anyvm_version} from {url}")
        urllib.request.urlretrieve(url, dest)

        # Ensure vendor/ is treated as a package
        init_path = os.path.join(vendor_dir, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w") as f:
                f.write("")

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:  # noqa: ARG002
        vendor_dir = os.path.join(
            os.path.dirname(__file__), "src", "anyvm_mcp", "vendor"
        )
        if os.path.isdir(vendor_dir):
            shutil.rmtree(vendor_dir)
