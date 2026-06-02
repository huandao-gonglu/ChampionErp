# Step 7 - Mercado Libre 官方类目刷新

## 本步目标

让“更新类目缓存”不再只是读取旧 JSON，而是具备从 Mercado Libre 官方接口同步类目树和必填属性，再写入 SQLite `category_cache` 的能力。

## 已完成

1. 新增官方类目同步器：
   - 读取 `sites/{site}/categories`
   - 递归读取 `categories/{category_id}`
   - 对叶子类目读取 `categories/{category_id}/attributes`
   - 组装为系统统一的 `category_cache` 记录
   - 导入 SQLite

2. `/api/category-cache/refresh` 已改为：
   - Mercado Libre：调用官方类目刷新
   - WB/Ozon：只读本地缓存，并明确返回 warning，不假装真实刷新

3. 前端“更新类目缓存”按钮已接入真实刷新接口：
   - 显示 SQLite 当前类目数量
   - 显示本次导入数量
   - 失败时显示明确错误

4. 接口授权处理：
   - 请求会带已保存的 Mercado Libre `access_token`
   - 如果官方接口返回 401/403，返回 `MERCADOLIBRE_CATEGORY_AUTH_REQUIRED`
   - 不写假成功日志，不覆盖旧缓存
   - 返回 `next_action`，前端会显示“前往授权页”的恢复入口

5. Playwright 覆盖授权缺失场景：
   - 点击“更新类目缓存”
   - 模拟 Mercado Libre 官方接口拒绝匿名访问
   - 页面显示授权恢复卡片
   - 用户可以直接点击“前往授权页”

## 当前真实状态

在当前机器上做真实接口冒烟时，Mercado Libre 返回：

```json
{
  "ok": false,
  "error_code": "MERCADOLIBRE_CATEGORY_AUTH_REQUIRED",
  "http_status": 401
}
```

这说明当前 ERP 里还没有可用的 Mercado Libre `access_token`，或 token 已失效。因此官方类目库尚未真正扩充，SQLite 仍保留原有 2 条 Mercado Libre 种子类目。

## 验证结果

- 15 个单元测试通过
- Playwright E2E 覆盖分类搜索与授权缺失恢复入口
- Python 编译通过
- 前端脚本语法检查通过
- `打开新版ERP.cmd` 未修改

## 下一步切入点

Step 8 建议先补 Mercado Libre 授权闭环：

1. 在设置页完成 Mercado Libre App ID / Client Secret / Redirect URI。
2. 生成授权链接并换取 `access_token`。
3. 再点“更新类目缓存”，让官方类目和属性真正写进 SQLite。
