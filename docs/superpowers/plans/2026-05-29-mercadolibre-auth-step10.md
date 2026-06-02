# Step 10 - Mercado Libre 授权页闭环打磨

## 当前进度

已经完成到 Step 10。

前置步骤已完成：

- SQLite 商品库和 6 张核心表
- 采集箱批量入口
- 平台草稿和状态机
- 批量 AI 文案状态回填
- 图片池持久化
- SQLite 类目缓存和分类弹窗
- Mercado Libre 官方类目刷新接口
- Playwright 浏览器自测
- 无 token 时类目刷新失败的恢复入口

## 本步目标

把 Mercado Libre 授权页和“刷新官方类目库”连接起来，让用户知道授权成功后下一步做什么。

## 已完成

1. 授权页新增“授权后下一步”卡片：
   - 标题：授权后下一步：刷新 Mercado Libre 官方类目库
   - 说明授权完成后回到发布预检页刷新类目缓存
   - 按钮：去刷新类目缓存

2. 前端按钮行为：
   - 点击“去刷新类目缓存”会切到发布预检页
   - 定位到类目属性区域
   - 提示用户完成授权后点击“更新类目缓存”

3. code 换 token 成功后：
   - 后端返回 `next_action`
   - 前端 toast 显示下一步：回到发布预检页刷新官方类目和必填属性
   - 临时 `code_verifier` 会在换 token 后清掉，不长期保存

4. 测试覆盖：
   - Playwright 验证授权页存在下一步卡片
   - Playwright 验证按钮能切到发布预检页
   - 单元测试验证 code 换 token 成功后返回 `next_action`

## 下一步切入点

Step 11 建议继续做“授权页真实验收辅助”：

1. 在授权页加一个“复制当前配置检查清单”。
2. 更清楚地区分 App ID、Client Secret、Redirect URI、code、token 的用途。
3. 对 `invalid_grant`、`redirect_uri mismatch`、`CODE_VERIFIER_MISSING` 这些常见错误做大白话解释。
