# Step 8 - Playwright 浏览器级自测

## 本步目标

安装官方 Playwright，并用浏览器自动化测试验证 ERP 前端分类弹窗真实可用。

## 官方包核验

安装的是 npm 官方包：

- 包名：`@playwright/test`
- 版本：`1.60.0`
- 作者：`Microsoft Corporation`
- 仓库：`https://github.com/microsoft/playwright`
- 官网：`https://playwright.dev`
- License：`Apache-2.0`

没有安装非官方同名包。

## 已完成

1. 新增 Node 测试配置：
   - `package.json`
   - `package-lock.json`
   - `playwright.config.js`

2. 新增 E2E 测试：
   - `tests/e2e/category-picker.spec.js`
   - 自动启动 `erp_web_app.py`
   - 使用本机 Chrome 运行 Playwright
   - 打开 `/publish`
   - 点击“选择分类”
   - 搜索“瓶”
   - 验证搜索结果命中“水瓶”
   - 点击结果后验证 `category_id = MLM-200`
   - 验证必填属性显示 `Brand`、`Model`

3. 修复页面直达路由：
   - `/collect`
   - `/library`
   - `/edit`
   - `/media`
   - `/pricing`
   - `/publish`
   - `/settings`
   - `/auth`
   - `/logs`

## 注意

`npx playwright install chromium` 下载 Chromium 时超时。当前 E2E 测试使用本机已安装的官方 Chrome 运行，Playwright 测试能力已安装并验证通过。

## 常用命令

```powershell
npm run test:e2e -- --reporter=list
```

## 验证结果

- Playwright E2E：通过
- Python 单元测试：通过
- Python 编译：通过
- 前端脚本语法：通过
- `打开新版ERP.cmd` 未修改
