# Step 11 - Mercado Libre 授权错误大白话解释

## 本步目标

把 Mercado Libre 授权失败从“技术错误”变成“可执行的修复建议”，避免用户看到 `invalid_grant`、`redirect_uri_mismatch`、`CODE_VERIFIER_MISSING` 后不知道下一步。

## 已完成

1. 后端新增统一解释器：
   - `explain_mercadolibre_auth_error`

2. 已覆盖常见错误：
   - `invalid_grant`：code 已失效、已用过、或粘贴太慢
   - `redirect_uri_mismatch`：ERP 和 Mercado Libre Developers 后台 Redirect URI 不一致
   - `CODE_VERIFIER_MISSING`：不是同一次授权链接生成和 code 换 token 流程
   - `token_expired` / `refresh_token_invalid`：token 过期或 refresh token 不可用
   - `invalid_client`：App ID / Client Secret 错误

3. 接口失败响应增强：
   - `/api/mercadolibre/exchange-code`
   - `/api/mercadolibre/refresh-token`
   - `/api/test-store-auth` 的 Mercado Libre 分支

   失败时返回：
   - `error_code`
   - `next_action`
   - `auth_explanation`

4. 前端授权页增强：
   - 新增 `renderAuthExplanation`
   - code 换 token 失败时显示红色解释卡片
   - refresh token 失败时显示红色解释卡片
   - Mercado Libre 店铺检测失败时显示红色解释卡片

5. Playwright 覆盖：
   - 模拟 `invalid_grant`
   - 页面显示“授权 code 已失效或已被使用”
   - 页面显示“重新生成授权链接”的下一步

## 下一步切入点

Step 12 建议清理授权页旧隐藏表单：

当前授权页里有一段隐藏旧表单，和新版授权向导重复使用了 `mlClientId`、`mlAuthDetail` 等 ID。Playwright 已经暴露这个问题。下一步应删除或改名隐藏旧表单，避免后续自动化和真实交互选错元素。
