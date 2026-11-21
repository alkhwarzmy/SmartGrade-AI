"""Checks for the direct Colab launcher without invoking Google account APIs."""

import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

import colab_direct as direct

ROOT = Path(__file__).resolve().parent


class DirectColabTests(unittest.TestCase):
    def setUp(self):
        direct.__dict__.pop("_smartgrade_direct_scope", None)
        direct.__dict__.pop("_smartgrade_direct_cells", None)

    def test_notebook_sources_are_valid_and_match_the_launcher(self):
        notebook = json.loads((ROOT / "notebooks/SmartGrade_Direct_Colab.ipynb").read_text())
        cells = [c for c in notebook["cells"] if c["cell_type"] == "code"]
        self.assertEqual(cells[0]["source"], (ROOT / "colab_direct.py").read_text())
        self.assertEqual(len({c["id"] for c in notebook["cells"]}), len(notebook["cells"]))
        for cell in cells:
            self.assertEqual(cell["outputs"], [])
            self.assertIsNone(cell["execution_count"])
            compile(direct._source(cell), cell["id"], "exec")

    def test_pinned_published_notebook_verifies_and_compiles(self):
        payload = (ROOT / "notebooks/SmartGrade_Institution_Colab.ipynb").read_bytes()
        with mock.patch.object(direct, "urlopen", return_value=io.BytesIO(payload)):
            cells = direct._fetch_notebook()
        self.assertEqual(len(cells), 10)

    def test_modified_download_is_rejected(self):
        payload = (ROOT / "notebooks/SmartGrade_Institution_Colab.ipynb").read_bytes() + b" "
        with mock.patch.object(direct, "urlopen", return_value=io.BytesIO(payload)):
            with self.assertRaises(ValueError):
                direct._fetch_notebook()

    def test_oversized_download_is_rejected(self):
        with mock.patch.object(direct, "urlopen", return_value=io.BytesIO(b"x" * (direct.MAX_NOTEBOOK_BYTES + 1))):
            with self.assertRaises(ValueError):
                direct._fetch_notebook()

    def test_restore_paths_are_values_and_do_not_execute_as_code(self):
        google = types.ModuleType("google")
        google.__path__ = []
        colab = types.ModuleType("google.colab")
        google.colab = colab
        restore = (
            'RESTORE_INSTITUTION = False\n'
            'RESTORE_ARCHIVE = ""\n'
            'RESTORE_KEY_FILE = ""\n'
            '_restore_probe = (RESTORE_INSTITUTION, RESTORE_ARCHIVE, RESTORE_KEY_FILE)\n'
        )
        archive = "/content/a'quoted-backup.sgb"
        key = '/content/a"quoted-key.key'
        with (
            mock.patch.dict(sys.modules, {"google": google, "google.colab": colab}),
            mock.patch.object(direct, "_fetch_notebook", return_value=[{"source": restore}]),
            mock.patch.object(direct, "RESTORE_PREVIOUS", True),
            mock.patch.object(direct, "RESTORE_FILE", archive),
            mock.patch.object(direct, "RESTORE_KEY", key),
        ):
            direct.start_project()
        self.assertEqual(
            direct._smartgrade_direct_scope["_restore_probe"],
            (True, archive, key),
        )

    def test_backup_uses_the_original_step_with_download_and_separate_key(self):
        direct._smartgrade_direct_scope = {}
        direct._smartgrade_direct_cells = [{
            "source": 'BACKUP_ACTION = "none"\nDOWNLOAD_BACKUP_KEY = False\n_probe = (BACKUP_ACTION, DOWNLOAD_BACKUP_KEY)\n'
        }]
        direct.backup_project()
        self.assertEqual(direct._smartgrade_direct_scope["_probe"], ("download", True))

    def test_commands_require_an_initialized_project(self):
        with self.assertRaises(RuntimeError):
            direct.backup_project()


if __name__ == "__main__":
    unittest.main()
