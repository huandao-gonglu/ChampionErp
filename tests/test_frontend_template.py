from __future__ import annotations

import re
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
FRONT_DIR = APP_DIR / "front"
FRONT_INDEX_PATH = FRONT_DIR / "index.html"
FRONT_SRC_DIR = FRONT_DIR / "src"


class FrontendTemplateTests(unittest.TestCase):
    def test_front_index_is_vite_vue_entry(self) -> None:
        html = FRONT_INDEX_PATH.read_text(encoding="utf-8")

        self.assertIn('<div id="app"></div>', html)
        self.assertIn('type="module" src="/src/main.ts"', html)
        self.assertIn("Champion ERP Flow", html)
        self.assertNotIn("cdn.tailwindcss.com", html)
        self.assertNotIn("__ACTIVE_PAGE__", html)

    def test_vue_entry_bootstraps_pinia_router_i18n_and_waits_for_router(self) -> None:
        main_ts = (FRONT_SRC_DIR / "main.ts").read_text(encoding="utf-8")

        self.assertIn("createApp", main_ts)
        self.assertIn("createPinia", main_ts)
        self.assertIn("await initI18n()", main_ts)
        self.assertIn("app.use(router)", main_ts)
        self.assertIn("app.use(i18n)", main_ts)
        self.assertIn("await router.isReady()", main_ts)
        self.assertIn("app.mount('#app')", main_ts)

    def test_app_vue_only_contains_global_shell(self) -> None:
        app_vue = (FRONT_SRC_DIR / "App.vue").read_text(encoding="utf-8")

        self.assertIn("<RouterView />", app_vue)
        self.assertIn("NavigationProgress", app_vue)
        self.assertIn("Toast", app_vue)
        self.assertNotIn("useWorkflowStore", app_vue)

    def test_router_uses_lazy_loaded_workflow_view_and_meta(self) -> None:
        router = (FRONT_SRC_DIR / "router" / "index.ts").read_text(encoding="utf-8")

        self.assertIn("createRouter", router)
        self.assertIn("createWebHistory", router)
        self.assertIn("import('@/views/workflow/WorkflowView.vue')", router)
        self.assertIn("titleKey", router)
        self.assertIn("router.beforeEach", router)
        self.assertIn("router.onError", router)

    def test_router_meta_types_are_declared(self) -> None:
        meta = (FRONT_SRC_DIR / "router" / "meta.d.ts").read_text(encoding="utf-8")

        self.assertIn("interface RouteMeta", meta)
        self.assertIn("requiresAuth", meta)
        self.assertIn("requiresAdmin", meta)
        self.assertIn("descriptionKey", meta)
        self.assertIn("hideInMenu", meta)

    def test_frontend_has_standard_framework_config_files(self) -> None:
        expected = [
            "package.json",
            "vite.config.ts",
            "vitest.config.ts",
            "tsconfig.json",
            "tailwind.config.js",
            "postcss.config.js",
            ".eslintrc.cjs",
        ]

        for relative in expected:
            with self.subTest(relative=relative):
                self.assertTrue((FRONT_DIR / relative).exists())

    def test_api_layer_is_centralized(self) -> None:
        client = (FRONT_SRC_DIR / "api" / "client.ts").read_text(encoding="utf-8")
        workflow_api = (FRONT_SRC_DIR / "api" / "workflow.ts").read_text(encoding="utf-8")

        self.assertIn("axios.create", client)
        self.assertIn("interceptors.request", client)
        self.assertIn("Authorization", client)
        self.assertIn("Accept-Language", client)
        self.assertIn("X-Timezone", client)
        self.assertIn("apiClient", workflow_api)
        self.assertIn("/api/state", workflow_api)

    def test_stores_are_split_into_app_auth_and_workflow(self) -> None:
        stores = sorted(path.name for path in (FRONT_SRC_DIR / "stores").glob("*.ts"))

        self.assertIn("app.ts", stores)
        self.assertIn("auth.ts", stores)
        self.assertIn("workflow.ts", stores)
        self.assertIn("index.ts", stores)

    def test_components_are_layered_by_responsibility(self) -> None:
        expected = [
            FRONT_SRC_DIR / "components" / "common" / "Toast.vue",
            FRONT_SRC_DIR / "components" / "common" / "NavigationProgress.vue",
            FRONT_SRC_DIR / "components" / "layout" / "AppLayout.vue",
            FRONT_SRC_DIR / "components" / "layout" / "AppHeader.vue",
            FRONT_SRC_DIR / "components" / "layout" / "AppSidebar.vue",
            FRONT_SRC_DIR / "components" / "layout" / "TablePageLayout.vue",
            FRONT_SRC_DIR / "components" / "auth" / "AuthSettingsPanel.vue",
            FRONT_SRC_DIR / "components" / "domain" / "CategoryPrecheckPanel.vue",
            FRONT_SRC_DIR / "views" / "workflow" / "WorkflowView.vue",
            FRONT_SRC_DIR / "views" / "workflow" / "CollectView.vue",
        ]

        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.exists())

    def test_i18n_uses_locale_modules_and_initializer(self) -> None:
        i18n = (FRONT_SRC_DIR / "i18n" / "index.ts").read_text(encoding="utf-8")

        self.assertIn("createI18n", i18n)
        self.assertIn("initI18n", i18n)
        self.assertIn("localStorage.getItem('locale')", i18n)
        self.assertTrue((FRONT_SRC_DIR / "i18n" / "locales" / "zh.ts").exists())
        self.assertTrue((FRONT_SRC_DIR / "i18n" / "locales" / "en.ts").exists())

    def test_tailwind_style_defines_shared_component_classes(self) -> None:
        style = (FRONT_SRC_DIR / "style.css").read_text(encoding="utf-8")

        self.assertIn("@tailwind base", style)
        self.assertIn("@layer components", style)
        for class_name in [".btn", ".btn-primary", ".btn-outline", ".input", ".card", ".badge-success"]:
            with self.subTest(class_name=class_name):
                self.assertIn(class_name, style)

    def test_vue_source_has_no_duplicate_static_ids(self) -> None:
        ids: list[str] = []
        for path in FRONT_SRC_DIR.rglob("*.vue"):
            ids.extend(re.findall(r'\bid="([^"]+)"', path.read_text(encoding="utf-8")))
        duplicates = sorted({item for item in ids if ids.count(item) > 1})

        self.assertEqual(duplicates, [])

    def test_python_backend_prefers_built_vue_dist_and_serves_assets(self) -> None:
        backend = (APP_DIR / "erp_web_app.py").read_text(encoding="utf-8-sig")
        static_routes = (APP_DIR / "routes" / "static_routes.py").read_text(encoding="utf-8")

        self.assertIn("FRONT_DIST_INDEX_PATH", backend)
        self.assertIn("FRONT_DIST_DIR", backend)
        self.assertIn("def serve_frontend_asset", static_routes)
        self.assertIn('parsed.path.startswith("/assets/")', backend)


if __name__ == "__main__":
    unittest.main()
