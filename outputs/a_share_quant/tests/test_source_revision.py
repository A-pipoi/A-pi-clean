import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quant.api_server import _current_git_revision, _frontend_source_revision


class SourceRevisionTests(unittest.TestCase):
    @patch("quant.api.audit_metadata.subprocess.run")
    def test_revision_marks_untracked_files_dirty(self, run):
        run.side_effect = [
            SimpleNamespace(stdout="0123456789ab\n"),
            SimpleNamespace(stdout="?? outputs/a_pi_site/dist/app.js\n"),
        ]

        with patch("quant.api_server._frontend_source_revision", return_value="fedcba987654"):
            self.assertEqual(_current_git_revision(), "0123456789ab+ui:fedcba987654-dirty")
        self.assertEqual(run.call_args_list[1].args[0], [
            "git", "status", "--porcelain", "--untracked-files=all"
        ])

    @patch("quant.api.audit_metadata.subprocess.run")
    def test_revision_falls_back_when_repository_is_unavailable(self, run):
        run.side_effect = OSError("git unavailable")

        self.assertEqual(_current_git_revision(), "unversioned")

    def test_frontend_revision_changes_when_served_asset_changes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary:
            site_dist = Path(temporary)
            (site_dist / "app.js").write_text("const version = 1;", encoding="utf-8")
            first = _frontend_source_revision(site_dist)
            (site_dist / "app.js").write_text("const version = 2;", encoding="utf-8")

            self.assertNotEqual(first, _frontend_source_revision(site_dist))


if __name__ == "__main__":
    unittest.main()
