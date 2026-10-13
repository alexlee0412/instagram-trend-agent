import os
import stat
import tempfile
import unittest

from export_instagram_cookies import _write_cookie_file


class CookieExportTests(unittest.TestCase):
    def test_cookie_file_is_owner_only_on_create_and_overwrite(self):
        with tempfile.TemporaryDirectory() as work_dir:
            output_path = os.path.join(work_dir, "cookies.txt")
            with open(output_path, "w", encoding="utf-8") as output:
                output.write("old")
            os.chmod(output_path, 0o644)

            _write_cookie_file(output_path, ["# Netscape HTTP Cookie File"])

            permissions = stat.S_IMODE(os.stat(output_path).st_mode)
            self.assertEqual(permissions, 0o600)
