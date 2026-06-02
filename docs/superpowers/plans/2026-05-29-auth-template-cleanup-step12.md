# Step 12 - 授权页隐藏旧表单清理

## 本步目标

清理授权页里隐藏的旧表单，解决重复 `id` 导致 Playwright 和真实交互可能选错元素的问题。

## 背景

Playwright 测试暴露出授权页存在重复 ID：

- `mlClientId`
- `mlClientSecret`
- `mlRedirectUri`
- `mlAuthDetail`
- `ml07DWizard`
- `wbContentToken`
- `ozonClientId`
- 等共 19 个重复项

这些重复项来自一个已经隐藏的旧版“店铺授权向导”表单。

## 已完成

1. 新增模板测试：
   - `test_template_has_no_duplicate_ids`
   - 扫描 `erp_web_template.html` 中所有 `id`
   - 如果出现重复 ID，测试失败

2. 删除隐藏旧表单：
   - 保留新版授权中心
   - 移除旧的隐藏 Mercado Libre / WB / Ozon 授权表单

3. Playwright 回归：
   - 分类弹窗仍可搜索并回填
   - 无 token 时类目刷新恢复入口仍可用
   - 授权页下一步入口仍可用
   - invalid_grant 大白话解释仍可用

## 下一步切入点

Step 13 建议进入“授权页配置检查清单”：

1. 增加一键复制当前 Mercado Libre 授权配置检查清单。
2. 明确显示 App ID、Client Secret、Redirect URI、code、token 的当前状态。
3. 帮用户授权前自查，减少 invalid_grant / redirect_uri_mismatch。
