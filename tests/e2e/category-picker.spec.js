const { test, expect } = require("@playwright/test");

test("category picker searches SQLite cache and fills attributes", async ({ page }) => {
  await page.goto("/publish");

  await page.route("**/api/category-attrs", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        required: [
          { id: "BRAND", name: "Brand", required: true },
          { id: "MODEL", name: "Model", required: true },
        ],
        optional: [],
      }),
    });
  });

  await page.route("**/api/category-precheck", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        errors: [],
      }),
    });
  });

  await expect(page.locator("#page-publish")).toBeVisible();
  await page.locator("#page-publish").getByRole("button", { name: "选择分类" }).click();

  await expect(page.locator("#categoryPickerModal")).toBeVisible();
  await page.locator("#categoryPickerKeyword").fill("瓶");
  await page.locator("#categoryPickerModal").getByRole("button", { name: "搜索" }).click();

  const firstResult = page.locator("#categorySearchResults .category-modal-result").first();
  await expect(firstResult).toContainText("水瓶");
  await expect(firstResult.locator("mark")).toContainText("瓶");
  await firstResult.click();

  await expect(page.locator("#categoryPickerModal")).toBeHidden();
  await expect(page.locator("#categoryId")).toHaveValue("MLM-200");
  await expect(page.locator("#categoryPath")).toHaveValue(/水瓶/);
  await expect(page.locator("#attrsBox")).toContainText("Brand");
  await expect(page.locator("#attrsBox")).toContainText("Model");
});

test("failed publish task with price field opens pricing page and highlights inputs", async ({ page }) => {
  await page.route("**/api/load-product", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        product: {
          product_id: "prod-price-1",
          name: "Price Fix Product",
          source: { title: "Price Fix Product", image_pool: [] },
          drafts: { mercadolibre: { enabled: true, title: "Titulo MX", description: "Descripcion MX" } },
          workflow_statuses: { mercadolibre: "ready_to_publish" },
        },
        imagePool: [],
        productsIndex: [],
      }),
    });
  });

  await page.goto("/library");
  await page.evaluate(() => {
    publishTaskEntries = [{
      product_id: "prod-price-1",
      product_title: "Price Fix Product",
      platform: "mercadolibre",
      job_id: "job-price-1",
      task_key: "prod-price-1::mercadolibre::job-price-1",
      platform_status: "failed",
      job_status: "completed",
      stage: "failed",
      error: "price missing",
      result: { error_map: { field_errors: { price: ["missing price"] } } },
    }];
    renderPublishTaskPanel();
  });

  await page.locator("#publishTaskPanel button[onclick^='goFixPublishTask']").click();
  await expect(page.locator("#page-pricing")).toBeVisible();
  await expect(page.locator("#purchaseCost")).toHaveClass(/task-fix-highlight/);
  await expect(page.locator("#pricingResultCard")).toHaveClass(/task-fix-highlight/);
});

test("publish page shows confirmation card before queue enqueue", async ({ page }) => {
  let enqueued = false;

  await page.route("**/api/category-attrs", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, required: [], optional: [] }),
    });
  });

  await page.route("**/api/publish-precheck", async route => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        platforms: {
          mercadolibre: { ok: true, errors: [], warnings: [] },
        },
        product: body.product,
        productsIndex: [],
      }),
    });
  });

  await page.route("**/api/publish-payload-preview", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        platform: "mercadolibre",
        status: "preview_only",
        path: "C:/Users/miami/Documents/Codex/2026-05-23/wb-10/output/last_mercadolibre_payload.json",
        payload: {
          title: "Titulo MX",
          category_id: "MLM-200",
          price: 19.99,
        },
      }),
    });
  });

  await page.route("**/api/publish-bus/enqueue", async route => {
    enqueued = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        job_id: "job-confirm-1",
        platforms: ["mercadolibre"],
        status: "queued",
      }),
    });
  });

  await page.goto("/publish");
  await page.fill("#categoryId", "MLM-200");
  await page.evaluate(() => renderPublishActionState());
  await page.locator("#publishCurrentBtn").click();

  await expect(page.locator("#publishConfirmCard")).toBeVisible();
  await expect(page.locator("#publishConfirmCard")).toContainText("last_mercadolibre_payload.json");
  expect(enqueued).toBeFalsy();

  await page.locator("#confirmPublishQueueBtn").click();
  await expect(page.locator("#publishBusStatus")).toContainText("job-confirm-1");
  expect(enqueued).toBeTruthy();
});

test("category refresh without Mercado Libre token shows an auth recovery action", async ({ page }) => {
  await page.goto("/publish");

  await page.route("**/api/category-cache/refresh", async route => {
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({
        ok: false,
        error_code: "MERCADOLIBRE_CATEGORY_AUTH_REQUIRED",
        error: "Mercado Libre 官方类目接口拒绝匿名访问，请先完成授权。",
        next_action: "前往授权页完成 Mercado Libre 授权，然后回到发布预检页刷新类目缓存。",
        cache_status: { storage: "sqlite", records: 2 },
      }),
    });
  });

  await page.locator("#page-publish").getByRole("button", { name: "更新类目缓存" }).click();

  await expect(page.locator("#categoryRefreshRecovery")).toBeVisible();
  await expect(page.locator("#categoryRefreshRecovery")).toContainText("Mercado Libre 授权");
  await expect(page.locator("#categoryRefreshRecovery").getByRole("button", { name: "前往授权页" })).toBeVisible();
});

test("auth page still links to publish page category refresh section", async ({ page }) => {
  await page.goto("/auth");

  await expect(page.locator("#mlCategoryRefreshNextStep")).toBeVisible();
  await page.locator("#mlCategoryRefreshNextStep").getByRole("button", { name: "去发布预检页查看" }).click();

  await expect(page.locator("#page-publish")).toBeVisible();
  await expect(page.locator("#categoryCacheStatus")).toBeVisible();
});

test("auth page explains invalid grant in plain language", async ({ page }) => {
  await page.goto("/auth");
  await page.route("**/api/mercadolibre/exchange-code", async route => {
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({
        ok: false,
        error: "invalid_grant",
        error_code: "invalid_grant",
        next_action: "重新生成授权链接，用主账号浏览器打开，拿新的 code 再换 token。",
        auth_explanation: {
          platform: "mercadolibre",
          code: "invalid_grant",
          title: "授权 code 已失效或已被使用",
          plain_message: "Mercado Libre 的 code 是一次性的，通常几分钟内有效。",
          next_action: "重新生成授权链接，用主账号浏览器打开，拿新的 code 再换 token。",
        },
      }),
    });
  });

  const authPage = page.locator("#page-auth");
  await authPage.locator("#mlClientId").first().fill("123");
  await authPage.locator("#mlClientSecret").first().fill("secret");
  await authPage.locator("#mlRedirectUri").first().fill("https://example.com/callback");
  await authPage.locator("#mlCallbackCode").first().fill("TG-used-code");
  await authPage.getByRole("button", { name: "用 code 换 token" }).first().click();

  await expect(authPage.locator("#mlAuthDetail").first()).toContainText("授权 code 已失效或已被使用");
  await expect(authPage.locator("#mlAuthDetail").first()).toContainText("重新生成授权链接");
});

test("auth page shows Mercado Libre config checklist", async ({ page }) => {
  await page.goto("/auth");

  await expect(page.locator("#mlAuthChecklist")).toBeVisible();
  await expect(page.locator("#mlAuthChecklist")).toContainText("App ID");
  await page.getByRole("button", { name: "刷新检查清单" }).click();
  await expect(page.locator("#mlAuthChecklist")).toContainText("下一步");
  await expect(page.getByRole("button", { name: "复制检查清单" })).toBeVisible();
});

test("auth page can directly refresh Mercado Libre category cache once token is ready", async ({ page }) => {
  await page.route("**/api/state", async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.storeConfig = data.storeConfig || {};
    data.storeConfig.mercadolibre = {
      ...(data.storeConfig.mercadolibre || {}),
      access_token: "test-access-token",
      refresh_token: "test-refresh-token",
      site_id: "MLM",
    };
    data.mercadolibreAuthChecklist = {
      ...(data.mercadolibreAuthChecklist || {}),
      ready_for_auth_link: true,
      token_ready: true,
      next_action: "可直接点击授权页里的立即刷新类目缓存按钮。",
    };
    await route.fulfill({ response, json: data });
  });

  await page.route("**/api/category-cache/refresh", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        imported: 18,
        cache_status: { storage: "sqlite", sqlite_records: 20, records: 20 },
      }),
    });
  });

  await page.goto("/auth");

  const refreshBtn = page.locator("#mlDirectCategoryRefreshBtn");
  await expect(refreshBtn).toBeEnabled();
  await refreshBtn.click();

  await expect(page.locator("#mlCategoryRefreshDirectStatus")).toContainText("18");
  await expect(page.locator("#mlCategoryRefreshDirectStatus")).toContainText("20");
});

test("category refresh success suggests a recommended keyword on publish page", async ({ page }) => {
  await page.route("**/api/state", async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.product = {
      ...(data.product || {}),
      category: "水瓶",
      source: {
        ...((data.product || {}).source || {}),
        title: "不锈钢水瓶",
      },
    };
    data.storeConfig = data.storeConfig || {};
    data.storeConfig.mercadolibre = {
      ...(data.storeConfig.mercadolibre || {}),
      access_token: "test-access-token",
      refresh_token: "test-refresh-token",
      site_id: "MLM",
    };
    data.mercadolibreAuthChecklist = {
      ...(data.mercadolibreAuthChecklist || {}),
      ready_for_auth_link: true,
      token_ready: true,
      next_action: "可直接刷新 Mercado Libre 类目缓存。",
    };
    await route.fulfill({ response, json: data });
  });

  await page.route("**/api/category-cache/refresh", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        imported: 18,
        cache_status: { storage: "sqlite", sqlite_records: 20, records: 20 },
      }),
    });
  });

  await page.route("**/api/category-search", async route => {
    const body = route.request().postDataJSON();
    expect(body.query).toBe("水瓶");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        results: [
          {
            category_id: "MLM-200",
            path_cn: "家居/厨房/水瓶",
            attributes_cache: {
              required: [{ id: "BRAND", name: "Brand", required: true }],
              optional: [],
            },
          },
        ],
        cache_status: { storage: "sqlite", sqlite_records: 20, records: 20 },
      }),
    });
  });

  await page.goto("/auth");
  await page.locator("#mlDirectCategoryRefreshBtn").click();
  await page.locator("#mlCategoryRefreshNextStep").getByRole("button", { name: "去发布预检页查看" }).click();

  await expect(page.locator("#categorySearchSuggestion")).toContainText("水瓶");
  await page.locator("#categorySearchSuggestion").getByRole("button", { name: "用推荐词搜索类目" }).click();

  await expect(page.locator("#categoryPickerModal")).toBeVisible();
  await expect(page.locator("#categoryPickerKeyword")).toHaveValue("水瓶");
  await expect(page.locator("#categorySearchResults")).toContainText("MLM-200");
});

test("selecting a category auto-loads attributes and runs local precheck", async ({ page }) => {
  await page.goto("/publish");

  await page.route("**/api/category-search", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        results: [
          {
            category_id: "MLM-200",
            path_cn: "家居/厨房/水瓶",
            attributes_cache: {
              required: [{ id: "BRAND", name: "Brand", required: true }],
              optional: [],
            },
          },
        ],
        cache_status: { storage: "sqlite", sqlite_records: 20, records: 20 },
      }),
    });
  });

  await page.route("**/api/category-attrs", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        required: [
          { id: "BRAND", name: "Brand", required: true },
          { id: "MODEL", name: "Model", required: true },
        ],
        optional: [{ id: "COLOR", name: "Color", required: false }],
      }),
    });
  });

  await page.route("**/api/category-precheck", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        errors: ["缺少必填属性：Brand", "缺少必填属性：Model"],
      }),
    });
  });

  await page.locator("#page-publish").getByRole("button", { name: "选择分类" }).click();
  await page.locator("#categoryPickerKeyword").fill("瓶");
  await page.locator("#categoryPickerModal").getByRole("button", { name: "搜索" }).click();
  await page.locator("#categorySearchResults .category-modal-result").first().click();

  await expect(page.locator("#attrsBox")).toContainText("Brand");
  await expect(page.locator("#attrsBox")).toContainText("Model");
  await expect(page.locator("#attrsBox")).toContainText("Color");
  await expect(page.locator("#categoryValidationBox")).toContainText("Brand");
  await expect(page.locator("#categoryValidationBox")).toContainText("Model");
});

test("missing required category attributes highlight fields and disable publish actions", async ({ page }) => {
  await page.goto("/publish");

  await page.route("**/api/category-search", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        results: [
          {
            category_id: "MLM-200",
            path_cn: "家居/厨房/水瓶",
            attributes_cache: {
              required: [{ id: "BRAND", name: "Brand", required: true }],
              optional: [],
            },
          },
        ],
        cache_status: { storage: "sqlite", sqlite_records: 20, records: 20 },
      }),
    });
  });

  await page.route("**/api/category-attrs", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        required: [
          { id: "BRAND", name: "Brand", required: true },
          { id: "MODEL", name: "Model", required: true },
        ],
        optional: [],
      }),
    });
  });

  await page.route("**/api/category-precheck", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        errors: ["缺少必填属性：Brand", "缺少必填属性：Model"],
      }),
    });
  });

  await page.locator("#page-publish").getByRole("button", { name: "选择分类" }).click();
  await page.locator("#categoryPickerKeyword").fill("瓶");
  await page.locator("#categoryPickerModal").getByRole("button", { name: "搜索" }).click();
  await page.locator("#categorySearchResults .category-modal-result").first().click();

  await expect(page.locator('[data-attr="BRAND"]')).toHaveClass(/border-red-300/);
  await expect(page.locator('[data-attr="MODEL"]')).toHaveClass(/border-red-300/);
  await expect(page.locator("#publishCurrentBtn")).toBeDisabled();
  await expect(page.locator("#publishSelectedBtn")).toBeDisabled();
});

test("publish precheck success promotes workflow status and syncs library list", async ({ page }) => {
  await page.goto("/publish");

  await page.route("**/api/publish-precheck", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        platforms: {
          mercadolibre: {
            ok: true,
            errors: [],
            warnings: [],
          },
        },
        product: {
          product_id: "prod-ready-1",
          name: "保温水瓶",
          source: {
            title: "保温水瓶",
            source_platform: "1688",
            source_url: "https://example.com/item",
            image_pool: [
              {
                id: "img_1",
                url: "https://example.com/1.jpg",
                preview_url: "https://example.com/1.jpg",
                origin: "ai_generated",
                usage: "main",
                platforms: ["mercadolibre"],
                is_main: true,
                selected: true,
                order: 0,
              },
            ],
          },
          drafts: {
            mercadolibre: {
              enabled: true,
              title: "Titulo MX",
              description: "Descripcion MX",
              category_id: "MLM-200",
              attributes: { BRAND: "BrandX", MODEL: "ModelY" },
              price: "19.99",
              stock: "5",
              publish_status: "ready",
              status: "ready_to_publish",
            },
          },
          workflow_statuses: {
            mercadolibre: "ready_to_publish",
          },
          publish_preview: {
            mercadolibre: {
              ok: true,
              errors: [],
              warnings: [],
            },
          },
        },
        productsIndex: [
          {
            product_id: "prod-ready-1",
            title: "保温水瓶",
            main_image: "https://example.com/1.jpg",
            source_platform: "1688",
            source_url: "https://example.com/item",
            platforms: ["mercadolibre"],
            collect_status: "success",
            workflow_status: "ready_to_publish",
            ai_copy_status: "done",
            image_status: "done",
            category_status: "done",
            attributes_status: "done",
            pricing_status: "done",
            precheck_status: true,
            publish_status: "ready",
            optimized: true,
          },
        ],
      }),
    });
  });

  await page.locator("#publishPreviewCard").getByRole("button", { name: "预检" }).first().click();

  await expect(page.locator("#publishPreviewCard")).toContainText("流程状态：校验通过");
  await expect(page.locator("#publishPreviewCard")).toContainText("预检状态：预检通过");

  await page.locator('button[data-page="library"]').click();
  await expect(page.locator("#libraryProductCard")).toContainText("校验通过");
});

test("library ready filter and batch queue entry only accept ready_to_publish items", async ({ page }) => {
  await page.route("**/api/state", async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.productsIndex = [
      {
        product_id: "prod-ready-1",
        title: "保温水瓶",
        main_image: "https://example.com/1.jpg",
        source_platform: "1688",
        source_url: "https://example.com/ready",
        platforms: ["mercadolibre"],
        workflow_status: "ready_to_publish",
        ai_copy_status: "done",
        image_status: "done",
        category_status: "done",
        attributes_status: "done",
        pricing_status: "done",
        precheck_status: true,
        publish_status: "ready",
        publish_queue_ready: true,
        publish_queue_platforms: ["mercadolibre"],
        optimized: true,
      },
      {
        product_id: "prod-pending-1",
        title: "待补齐属性的水瓶",
        main_image: "https://example.com/2.jpg",
        source_platform: "1688",
        source_url: "https://example.com/pending",
        platforms: ["mercadolibre"],
        workflow_status: "images_ready",
        ai_copy_status: "done",
        image_status: "done",
        category_status: "done",
        attributes_status: "done",
        pricing_status: "done",
        precheck_status: false,
        publish_status: "not_ready",
        publish_queue_ready: false,
        publish_queue_platforms: [],
        optimized: true,
      },
    ];
    await route.fulfill({ response, json: data });
  });

  await page.route("**/api/load-product", async route => {
    const body = route.request().postDataJSON();
    expect(body.product_id).toBe("prod-ready-1");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        product: {
          product_id: "prod-ready-1",
          name: "保温水瓶",
          source: {
            title: "保温水瓶",
            source_platform: "1688",
            source_url: "https://example.com/ready",
            image_pool: [],
          },
          drafts: {
            mercadolibre: {
              enabled: true,
              title: "Titulo MX",
              description: "Descripcion MX",
              status: "ready_to_publish",
              publish_status: "ready",
            },
          },
          workflow_statuses: { mercadolibre: "ready_to_publish" },
        },
        productsIndex: [],
      }),
    });
  });

  await page.route("**/api/publish-bus/enqueue", async route => {
    const body = route.request().postDataJSON();
    expect(body.platforms).toEqual(["mercadolibre"]);
    expect(body.product.product_id).toBe("prod-ready-1");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        job_id: "job-ready-1",
        platforms: ["mercadolibre"],
        status: "queued",
      }),
    });
  });

  await page.goto("/library");

  await page.locator("#libraryWorkflowFilter").selectOption("ready_to_publish");
  await expect(page.locator("#libraryProductCard tbody tr")).toHaveCount(1);
  await expect(page.locator("#libraryProductCard")).toContainText("校验通过");

  await page.locator(".library-row-check").first().check();
  await expect(page.locator("#libraryPublishReadyBtn")).toBeEnabled();
  await page.locator("#libraryPublishReadyBtn").click();

  await expect(page.locator("#libraryPublishSummary")).toContainText("1 个已通过预检");
  await expect(page.locator("#publishBusStatus")).toContainText("job-ready-1");
});

test("publish task panel shows batch job statuses and can retry failed items", async ({ page }) => {
  let enqueueCount = 0;
  await page.route("**/api/state", async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.productsIndex = [
      {
        product_id: "prod-ready-1",
        title: "保温水瓶",
        main_image: "https://example.com/1.jpg",
        source_platform: "1688",
        source_url: "https://example.com/ready",
        platforms: ["mercadolibre"],
        workflow_status: "ready_to_publish",
        ai_copy_status: "done",
        image_status: "done",
        category_status: "done",
        attributes_status: "done",
        pricing_status: "done",
        precheck_status: true,
        publish_status: "ready",
        publish_queue_ready: true,
        publish_queue_platforms: ["mercadolibre"],
        optimized: true,
      },
    ];
    await route.fulfill({ response, json: data });
  });

  await page.route("**/api/load-product", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        product: {
          product_id: "prod-ready-1",
          name: "保温水瓶",
          source: {
            title: "保温水瓶",
            source_platform: "1688",
            source_url: "https://example.com/ready",
            image_pool: [],
          },
          drafts: {
            mercadolibre: {
              enabled: true,
              title: "Titulo MX",
              description: "Descripcion MX",
              status: "ready_to_publish",
              publish_status: "ready",
            },
          },
          workflow_statuses: { mercadolibre: "ready_to_publish" },
        },
        productsIndex: [],
      }),
    });
  });

  await page.route("**/api/publish-bus/enqueue", async route => {
    enqueueCount += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        job_id: enqueueCount === 1 ? "job-failed-1" : "job-retry-1",
        platforms: ["mercadolibre"],
        status: "queued",
      }),
    });
  });

  await page.route("**/api/publish-bus/status?job_id=job-failed-1", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        job: {
          job_id: "job-failed-1",
          status: "completed",
          platforms: {
            mercadolibre: {
              platform: "mercadolibre",
              status: "failed",
              stage: "failed",
              error: "缺少主图",
              attempts: 1,
            },
          },
        },
      }),
    });
  });

  await page.route("**/api/publish-bus/status?job_id=job-retry-1", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        job: {
          job_id: "job-retry-1",
          status: "completed",
          platforms: {
            mercadolibre: {
              platform: "mercadolibre",
              status: "success",
              stage: "finished",
              error: "",
              attempts: 1,
            },
          },
        },
      }),
    });
  });

  await page.goto("/library");
  await page.locator(".library-row-check").first().check();
  await page.locator("#libraryPublishReadyBtn").click();

  await page.locator("#publishTaskRefreshBtn").click();
  await expect(page.locator("#publishTaskPanel")).toContainText("job-failed-1");
  await expect(page.locator("#publishTaskPanel")).toContainText("failed");
  await expect(page.locator("#publishTaskPanel")).toContainText("缺少主图");

  await page.locator("#publishTaskPanel").getByRole("button", { name: "重试" }).click();
  await page.locator("#publishTaskRefreshBtn").click();

  await expect(page.locator("#publishTaskPanel")).toContainText("job-retry-1");
  await expect(page.locator("#publishTaskPanel")).toContainText("success");
  await expect.poll(async () => page.evaluate(() => {
    const record = state.productsIndex.find(item => item.product_id === "prod-ready-1") || {};
    return `${record.publish_status || ""}/${record.workflow_status || ""}/${record.last_publish_job_id || ""}`;
  })).toBe("published/published/job-retry-1");
});

test("failed publish task shows friendly error and can jump to fix section", async ({ page }) => {
  await page.route("**/api/state", async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.productsIndex = [
      {
        product_id: "prod-ready-1",
        title: "保温水瓶",
        main_image: "https://example.com/1.jpg",
        source_platform: "1688",
        source_url: "https://example.com/ready",
        platforms: ["mercadolibre"],
        workflow_status: "ready_to_publish",
        ai_copy_status: "done",
        image_status: "done",
        category_status: "done",
        attributes_status: "done",
        pricing_status: "done",
        precheck_status: true,
        publish_status: "ready",
        publish_queue_ready: true,
        publish_queue_platforms: ["mercadolibre"],
        optimized: true,
      },
    ];
    await route.fulfill({ response, json: data });
  });

  await page.route("**/api/load-product", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        product: {
          product_id: "prod-ready-1",
          name: "保温水瓶",
          source: {
            title: "保温水瓶",
            source_platform: "1688",
            source_url: "https://example.com/ready",
            image_pool: [],
          },
          drafts: {
            mercadolibre: {
              enabled: true,
              title: "Titulo MX",
              description: "Descripcion MX",
              status: "ready_to_publish",
              publish_status: "ready",
            },
          },
          workflow_statuses: { mercadolibre: "ready_to_publish" },
        },
        productsIndex: [],
      }),
    });
  });

  await page.route("**/api/publish-bus/enqueue", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        job_id: "job-failed-fix-1",
        platforms: ["mercadolibre"],
        status: "queued",
      }),
    });
  });

  await page.route("**/api/publish-bus/status?job_id=job-failed-fix-1", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        job: {
          job_id: "job-failed-fix-1",
          status: "completed",
          platforms: {
            mercadolibre: {
              platform: "mercadolibre",
              status: "failed",
              stage: "failed",
              error: "缺少主图",
              attempts: 1,
              result: {
                error_map: {
                  summary: "发布失败",
                  field_errors: {
                    images: ["缺少主图"],
                  },
                },
              },
            },
          },
        },
      }),
    });
  });

  await page.goto("/library");
  await page.locator(".library-row-check").first().check();
  await page.locator("#libraryPublishReadyBtn").click();
  await page.locator("#publishTaskRefreshBtn").click();

  await expect(page.locator("#publishTaskPanel")).toContainText("缺少主图，请先去图片区设置主图。");
  await page.locator("#publishTaskPanel").getByRole("button", { name: "去修复" }).click();

  await expect(page.locator("#page-edit")).toBeVisible();
  await expect(page.locator("#taskFixContextBanner")).toContainText("缺少主图，请先去图片区设置主图。");
  await expect(page.locator("#section-images")).toHaveClass(/task-fix-highlight/);
});
