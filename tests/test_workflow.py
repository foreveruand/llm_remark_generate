from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseWorkflowTest(unittest.TestCase):
    def test_release_workflow_checks_out_fixed_package_path(self) -> None:
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
        content = workflow.read_text(encoding="utf-8")

        self.assertIn("working-directory: ankiplugin", content)
        self.assertIn("path: ankiplugin", content)
        self.assertIn("ankiplugin/dist/llm_remark_generator.ankiaddon", content)
        self.assertIn("pyinstaller --onefile", content)
        self.assertIn("llm-document-converter.exe", content)


if __name__ == "__main__":
    unittest.main()
