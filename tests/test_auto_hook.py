# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for in-venv .pth auto-hooking, agy_watch watch, unwatch, and status commands."""

import os
import shutil
import tempfile
import pytest
from click.testing import CliRunner

from agy_watch.cli import main as cli_main, _resolve_site_packages
import agy_watch.cli as cli_module


def test_resolve_site_packages_and_cli_watch_unwatch():
    """Verifies that agy_watch watch and unwatch properly manage .pth hooks in virtual environments."""
    temp_dir = tempfile.mkdtemp(prefix="agy_hook_test_")
    try:
        # Create a fake virtual environment structure
        venv_dir = os.path.join(temp_dir, "my_project", ".venv")
        site_packages = os.path.join(venv_dir, "lib", "python3.13", "site-packages")
        os.makedirs(site_packages, exist_ok=True)

        # Mock watched envs storage file
        watched_json = os.path.join(temp_dir, "watched_envs.json")
        cli_module.WATCHED_ENVS_PATH = watched_json

        runner = CliRunner()

        # 1. Test watch command on target project folder
        res_watch = runner.invoke(cli_main, ["watch", os.path.join(temp_dir, "my_project")])
        assert res_watch.exit_code == 0
        assert "Successfully installed agy_watch auto-hook" in res_watch.output

        pth_file = os.path.join(site_packages, "agy_watch_hook.pth")
        assert os.path.exists(pth_file)
        with open(pth_file, "r") as f:
            assert "import agy_watch.auto_hook" in f.read()

        # 2. Test status command
        res_status = runner.invoke(cli_main, ["status"])
        assert res_status.exit_code == 0
        assert "[ACTIVE]" in res_status.output
        assert site_packages in res_status.output

        # 3. Test proxy-path command (must output only the path)
        res_proxy = runner.invoke(cli_main, ["proxy-path"])
        assert res_proxy.exit_code == 0
        proxy_path = res_proxy.output.strip()
        assert proxy_path.endswith("agy-harness-proxy") or proxy_path.endswith("proxy.py")

        # 4. Test unwatch command
        res_unwatch = runner.invoke(cli_main, ["unwatch", os.path.join(temp_dir, "my_project")])
        assert res_unwatch.exit_code == 0
        assert "Successfully removed agy_watch auto-hook" in res_unwatch.output
        assert not os.path.exists(pth_file)

        # 5. Verify status is now clean
        res_status2 = runner.invoke(cli_main, ["status"])
        assert res_status2.exit_code == 0
        assert "No environments currently watched" in res_status2.output

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
