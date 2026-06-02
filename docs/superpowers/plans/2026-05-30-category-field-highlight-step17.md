# Step 17 - 字段级高亮与发布按钮锁定

日期：2026-05-30

## 这一步做了什么

1. 类目属性区从“纯文本报错”升级成字段级校验：
   - 缺失的必填属性输入框会直接标红
   - 输入框下方会显示简短提示
2. `categoryValidationBox` 现在会显示结构化校验状态：
   - 未选类目时提示先选类目
   - 缺少必填属性时显示缺失项汇总
   - 通过时显示 `属性预检通过`
3. 发布操作按钮现在会联动锁定：
   - `发布当前平台`
   - `发布全部已勾选平台`
   - `确认真实发布 Mercado Libre`
4. 锁定规则当前包括：
   - 未选择类目
   - 当前类目存在缺失的必填属性
5. 就算通过手动触发前端函数，发布动作也会先检查 blocker，不会直接继续执行。

## 涉及文件

- `C:\Users\miami\Documents\Codex\2026-05-23\wb-10\erp_web_template.html`
- `C:\Users\miami\Documents\Codex\2026-05-23\wb-10\tests\test_frontend_template.py`
- `C:\Users\miami\Documents\Codex\2026-05-23\wb-10\tests\e2e\category-picker.spec.js`

## 本步验证

- Python 单元测试：`24` 个通过
- Python 编译检查：通过
- 前端内联脚本语法检查：通过
- Playwright E2E：`9` 个通过
- `打开新版ERP.cmd` 未修改

## 当前结果

现在发布预检页已经开始有“强校验”手感了：不是只告诉你哪里有问题，而是直接把问题字段标出来，并在没补齐之前把发布按钮锁住。

## 下一步建议

Step 18：把属性高亮和平台草稿状态机串起来，自动把草稿状态从 `images_ready` 推进到 `ready_to_publish`，并在商品库列表里同步显示。

建议模型：`5.4 高`
