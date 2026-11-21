from __future__ import annotations

import re
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
FRONT_DIR = APP_DIR / "front"
FRONT_INDEX_PATH = FRONT_DIR / "index.html"
FRONT_SRC_DIR = FRONT_DIR / "src"


class FrontendTemplateTests(unittest.TestCase):
    def test_vue_source_has_no_duplicate_static_ids(self) -> None:
        ids: list[str] = []
        for path in FRONT_SRC_DIR.rglob("*.vue"):
            ids.extend(re.findall(r'(?<![-:\w])id="([^"]+)"', path.read_text(encoding="utf-8")))
        duplicates = sorted({item for item in ids if ids.count(item) > 1})

        self.assertEqual(duplicates, [])

    def test_backend_resources_stay_with_code_when_business_directory_changes(self) -> None:
        from erp_web.context import AppPaths, get_context
        business = get_context().paths.app_dir
        paths = AppPaths.from_app_dir(business)
        self.assertEqual(paths.db_path.parent, business)
        self.assertEqual(paths.front_dist_dir, APP_DIR / 'erp_web' / 'static' / 'dist')
        self.assertEqual(paths.web_template_path, FRONT_INDEX_PATH)
        self.assertFalse(paths.front_dist_dir.is_relative_to(business))


if __name__ == "__main__":
    unittest.main()
