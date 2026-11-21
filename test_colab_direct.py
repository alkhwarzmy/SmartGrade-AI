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
        payload = (ROOT / "notebooks/SmartGrade_Legacy_Source.ipynb").read_bytes()
        with mock.patch.object(direct, "urlopen", return_value=io.BytesIO(payload)):
            cells = direct._fetch_notebook()
        self.assertEqual(len(cells), 10)

    def test_modified_download_is_rejected(self):
        payload = (ROOT / "notebooks/SmartGrade_Legacy_Source.ipynb").read_bytes() + b" "
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

    def test_default_display_keeps_launch_settings_and_uses_a_window(self):
        notebook = json.loads((ROOT / "notebooks/SmartGrade_Legacy_Source.ipynb").read_text())
        original = next(
            direct._source(cell) for cell in notebook["cells"]
            if cell["cell_type"] == "code" and "output.serve_kernel_port_as_iframe(" in direct._source(cell)
        )
        displayed = direct._display_source(original)
        self.assertIn("output.serve_kernel_port_as_window(PORT)", displayed)
        self.assertIn("launch_institution(", displayed)
        self.assertIn("browser_address=address", displayed)
        self.assertNotIn("enableXsrfProtection", displayed)

    def test_embedded_display_can_be_selected_explicitly(self):
        source = "output.serve_kernel_port_as_iframe(PORT, height=1000, cache_in_notebook=False)"
        with mock.patch.object(direct, "OPEN_IN_NEW_TAB", False):
            self.assertEqual(direct._display_source(source), source)

    def test_window_uses_the_running_notebook_port(self):
        google = types.ModuleType("google")
        google.__path__ = []
        colab = types.ModuleType("google.colab")
        output = types.SimpleNamespace(serve_kernel_port_as_window=mock.Mock())
        colab.output = output
        google.colab = colab
        direct._smartgrade_direct_scope = {"PORT": 8655}
        with mock.patch.dict(sys.modules, {"google": google, "google.colab": colab}):
            direct.open_project_window()
        output.serve_kernel_port_as_window.assert_called_once_with(8655)

    def test_commands_require_an_initialized_project(self):
        with self.assertRaises(RuntimeError):
            direct.backup_project()


if __name__ == "__main__":
    unittest.main()
