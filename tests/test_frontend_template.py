from __future__ import annotations

import re
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]


class FrontendTemplateTests(unittest.TestCase):
    def test_category_picker_modal_and_sqlite_attr_rendering_are_present(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn('id="categoryPickerModal"', html)
        self.assertIn("openCategoryPicker", html)
        self.assertIn("renderCategorySearchResults", html)
        self.assertIn('attrs.required || []', html)
        self.assertIn('attrs.optional || []', html)

    def test_auth_page_has_direct_category_refresh_controls(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn('id="mlDirectCategoryRefreshBtn"', html)
        self.assertIn('id="mlCategoryRefreshDirectStatus"', html)
        self.assertIn("refreshCategoryCacheFromAuth", html)

    def test_publish_page_has_category_search_suggestion_flow(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn('id="categorySearchSuggestion"', html)
        self.assertIn("applyRecommendedCategorySearch", html)
        self.assertIn("recommendedCategorySearchKeyword", html)

    def test_collect_updates_frontend_image_pool_from_api_result(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn('state.imagePool = result.imagePool || (((state.product || {}).source || {}).image_pool || []);', html)
        self.assertIn('state.product = result.product || result || state.product;', html)

    def test_category_selection_auto_loads_attrs_and_runs_precheck(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn("runCategorySelectionChecks", html)
        self.assertIn("正在读取必填属性并执行本地预检", html)
        self.assertIn('selectCategoryResult(${index}).catch', html)

    def test_category_validation_can_highlight_fields_and_lock_publish_buttons(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn('id="publishCurrentBtn"', html)
        self.assertIn('id="publishSelectedBtn"', html)
        self.assertIn("missingCategoryAttributeKeys", html)
        self.assertIn("renderPublishActionState", html)
        self.assertIn("border-red-300 bg-red-50", html)

    def test_publish_preview_and_library_show_workflow_status_sync(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn("流程状态：${escapeHtml(workflowStatusLabel(workflowStatus))}", html)
        self.assertIn("workflow_status", html)
        self.assertIn("workflowStatusLabel(item.workflow_status)", html)

    def test_library_has_ready_to_publish_filter_and_batch_queue_entry(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn('id="libraryWorkflowFilter"', html)
        self.assertIn('id="libraryPublishReadyBtn"', html)
        self.assertIn('id="libraryPublishSummary"', html)
        self.assertIn("publishSelectedLibraryReady", html)
        self.assertIn("publish_queue_ready", html)

    def test_publish_task_panel_and_retry_hooks_are_present(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn('id="publishTaskPanel"', html)
        self.assertIn("renderPublishTaskPanel", html)
        self.assertIn("refreshPublishTaskPanel", html)
        self.assertIn("retryPublishTask", html)
        self.assertIn("publishTaskEntries", html)
        self.assertIn("taskFriendlyError", html)
        self.assertIn("goFixPublishTask", html)
        self.assertIn("taskFixContextBanner", html)
        self.assertIn("applyTaskFixHighlights", html)
        self.assertIn("task-fix-highlight", html)
        self.assertIn("attr:", html)
        self.assertIn("pageForTaskFixSection", html)

    def test_publish_before_queue_requires_confirmation_card(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn("pendingPublishConfirmation", html)
        self.assertIn("preparePublishConfirmation", html)
        self.assertIn("renderPublishConfirmation", html)
        self.assertIn('id="confirmPublishQueueBtn"', html)
        self.assertIn("confirmPendingPublishQueue", html)
        self.assertIn("确认前不会入队，也不会写成功日志", html)

    def test_publish_task_results_write_back_to_page_state(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn("applyPublishTaskResultsToState", html)
        self.assertIn("applyPublishTaskResultToProduct", html)
        self.assertIn("last_publish_task", html)
        self.assertIn("last_publish_job_id", html)

    def test_publish_page_collapses_debug_blocks_into_ops_panels(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        self.assertIn("renderJsonDetailsCard", html)
        self.assertIn("运维 / 调试快照", html)
        self.assertIn("队列状态摘要", html)
        self.assertIn('id="publishDebugJson"', html)

    def test_template_has_no_duplicate_ids(self) -> None:
        html = (APP_DIR / "erp_web_template.html").read_text(encoding="utf-8")

        ids = re.findall(r'\bid="([^"]+)"', html)
        duplicates = sorted({item for item in ids if ids.count(item) > 1})

        self.assertEqual(duplicates, [])


if __name__ == "__main__":
    unittest.main()
