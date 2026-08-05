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

"""Global pytest fixtures for agy_watch test isolation."""

import os
import shutil
import tempfile
import pytest


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path, monkeypatch):
    """Ensures that all tests write to temporary, isolated SQLite registry and settings files.

    Prevents tests from modifying or creating test entries in the user's
    real ~/.antigravity/samples/agy_watch/registry.db or settings.json.
    """
    test_dir = tmp_path / "agy_watch_test_env"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_db_path = str(test_dir / "registry.db")
    test_settings_path = str(test_dir / "settings.json")

    monkeypatch.setenv("AGY_WATCH_REGISTRY_DB", test_db_path)
    monkeypatch.setenv("AGY_WATCH_SETTINGS_PATH", test_settings_path)

    import agy_watch.registry
    import agy_watch.settings
    agy_watch.registry._default_registry = None
    agy_watch.settings._global_settings = None

    yield {
        "registry_db": test_db_path,
        "settings_path": test_settings_path,
    }

    agy_watch.registry._default_registry = None
    agy_watch.settings._global_settings = None
