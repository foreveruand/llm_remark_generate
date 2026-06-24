from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ankiplugin.package_addon import ADDON_NAME, ADDON_PACKAGE, build_addon


class PackageAddonTest(unittest.TestCase):
    def test_package_has_anki_manifest_and_root_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "llm_remark_generator.ankiaddon"

            build_addon(output)

            with ZipFile(output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("manifest.json"))

        self.assertIn("__init__.py", names)
        self.assertIn("config_dialog.py", names)
        self.assertIn("documents.py", names)
        self.assertIn("manifest.json", names)
        self.assertNotIn("converter/document_converter.py", names)
        self.assertNotIn(f"{ADDON_PACKAGE}/__init__.py", names)
        self.assertFalse(any("__pycache__" in name for name in names))
        self.assertEqual(ADDON_PACKAGE, manifest["package"])
        self.assertEqual(ADDON_NAME, manifest["name"])


if __name__ == "__main__":
    unittest.main()
