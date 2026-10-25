"""Exercise committed Git files and the ZIP layout HACS extracts into sigen."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_release.py"
SPEC = importlib.util.spec_from_file_location("sigen_release_builder", SCRIPT_PATH)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
build_release = BUILDER.build_release


class TestReleasePackage(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)
        self.source = self.repository / "custom_components" / "sigen"
        self.source.mkdir(parents=True)
        self.output = self.repository / "dist" / "sigen.zip"
        self.files = {
            "manifest.json": json.dumps({"domain": "sigen", "version": "1.3.0b2"}),
            "__init__.py": '"""Integration entry point."""\n',
            "sensor.py": 'NAME = "Sigenergy"\n',
            "translations/en.json": '{"title": "Sigenergy ESS"}\n',
        }
        for name, content in self.files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (self.repository / "hacs.json").write_text(json.dumps({
            "name": "Sigenergy ESS", "zip_release": True, "filename": "sigen.zip",
        }), encoding="utf-8")
        (self.repository / "README.md").write_text("Not an installation file", encoding="utf-8")
        self.git("init", "-q")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "release-test@example.invalid")
        self.commit()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repository, check=True, capture_output=True,
        ).stdout

    def commit(self):
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "Test release snapshot")

    def test_zip_installs_at_the_expected_hacs_path_and_preserves_adjacent_data(self):
        result = build_release(self.repository, "v.1.3.0b2", self.output)
        configuration = self.repository / "ha-config"
        target = configuration / "custom_components" / "sigen"
        target.mkdir(parents=True)
        registry = configuration / ".storage" / "core.entity_registry"
        registry.parent.mkdir()
        registry.write_text("existing entity IDs and configuration", encoding="utf-8")
        sibling = target.parent / "another_integration" / "__init__.py"
        sibling.parent.mkdir()
        sibling.write_text("existing integration", encoding="utf-8")

        with zipfile.ZipFile(self.output) as package:
            self.assertEqual(set(self.files), set(package.namelist()))
            # HACS extracts release ZIPs straight into the domain directory.
            package.extractall(target)
        for name, content in self.files.items():
            self.assertEqual(content, (target / name).read_text(encoding="utf-8"))
        self.assertFalse((target / "custom_components").exists())
        self.assertEqual("existing entity IDs and configuration", registry.read_text(encoding="utf-8"))
        self.assertEqual("existing integration", sibling.read_text(encoding="utf-8"))
        self.assertEqual("true", result["prerelease"])

    def test_package_uses_committed_files_and_ignores_local_caches(self):
        committed_sensor = self.git("show", "HEAD:custom_components/sigen/sensor.py")
        (self.source / "sensor.py").write_text("uncommitted modification", encoding="utf-8")
        cache = self.source / "__pycache__" / "sensor.pyc"
        cache.parent.mkdir()
        cache.write_bytes(b"local cache")
        build_release(self.repository, "v.1.3.0b2", self.output)
        with zipfile.ZipFile(self.output) as package:
            self.assertEqual(committed_sensor, package.read("sensor.py"))
            self.assertFalse(any("__pycache__" in name for name in package.namelist()))

    def test_identical_commits_produce_identical_packages(self):
        first = build_release(self.repository, "v.1.3.0b2", self.output)
        second = build_release(self.repository, "v.1.3.0b2", self.repository / "other.zip")
        self.assertEqual(first["sha256"], second["sha256"])

    def test_wrong_tag_is_rejected_before_an_archive_is_written(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_release(self.repository, "v.1.3.0b3", self.output)
        self.assertFalse(self.output.exists())

    def test_missing_zip_configuration_is_rejected(self):
        (self.repository / "hacs.json").write_text('{"name": "Sigenergy ESS"}', encoding="utf-8")
        self.commit()
        with self.assertRaisesRegex(ValueError, "sigen.zip"):
            build_release(self.repository, "v.1.3.0b2", self.output)
        self.assertFalse(self.output.exists())

    def test_stable_four_part_version_is_not_a_prerelease(self):
        manifest = self.source / "manifest.json"
        manifest.write_text(json.dumps({"domain": "sigen", "version": "1.3.0.0"}), encoding="utf-8")
        self.commit()
        result = build_release(self.repository, "v.1.3.0.0", self.output)
        self.assertEqual("false", result["prerelease"])


if __name__ == "__main__":
    unittest.main()
