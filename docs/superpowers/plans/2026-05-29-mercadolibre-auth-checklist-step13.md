# Step 13 - Mercado Libre 授权配置检查清单

## 本步目标

在授权页增加一份可见、可复制的 Mercado Libre 授权配置检查清单，减少授权前的配置错误。

## 已完成

1. 后端新增：
   - `mercadolibre_auth_checklist`
   - `/api/mercadolibre/auth-checklist`

2. 检查内容：
   - App ID / Client ID
   - Client Secret
   - Redirect URI
   - Site
   - code_verifier
   - Access Token
   - Refresh Token

3. 输出能力：
   - `ready_for_auth_link`
   - `token_ready`
   - `missing_codes`
   - `next_action`
   - `copy_text`

4. 前端授权页新增：
   - “授权配置检查清单”卡片
   - “刷新检查清单”按钮
   - “复制检查清单”按钮

5. 测试覆盖：
   - 单元测试验证缺失字段与 copy_text
   - Playwright 验证授权页显示检查清单、刷新按钮、复制按钮

## 当前建议模型等级

本步属于中等复杂度：后端结构化输出 + 前端展示 + Playwright 验证。

建议模型：5.4 高。

## 下一步切入点

Step 14 建议开始做“授权成功后自动刷新官方类目”的半自动闭环：

1. code 换 token 成功后，页面出现“立即刷新类目缓存”按钮。
2. 点击后调用官方类目刷新。
3. 成功后显示导入数量，并跳回分类搜索弹窗验证结果。
