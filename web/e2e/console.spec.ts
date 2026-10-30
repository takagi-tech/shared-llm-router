import { test, expect } from "@playwright/test";

test("admin workflow: registry, keys, requests, measurements and monochrome layout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/admin");
  await page
    .getByLabel("パスワード", { exact: true })
    .fill("test-password-123");
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "推論環境の概況" }),
  ).toBeVisible();
  await expect(page.getByText("最初の接続先を登録しましょう")).toBeVisible();
  await page.getByRole("link", { name: "モデル確認", exact: true }).click();
  await page.getByRole("button", { name: "サーバを登録", exact: true }).click();
  await page.getByLabel("管理名", { exact: true }).fill("test-server");
  await page.getByLabel("ホスト名 / IP").fill("test-gpu");
  await page.getByRole("button", { name: "保存する" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "接続先を登録", exact: true }).click();
  await page.getByLabel("管理名", { exact: true }).fill("test-endpoint");
  await page
    .getByLabel("Base URL", { exact: true })
    .fill("http://127.0.0.1:18081/fixture/v1");
  await page.getByLabel("このCapacityで有効にする").check();
  await page.getByRole("button", { name: "保存する" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "接続確認", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "正常", exact: true }),
  ).toBeVisible();
  // Hidden from the menu; the page is still reachable by URL.
  await page.goto("/admin/aliases");
  await page.getByRole("button", { name: "エイリアスを登録" }).click();
  await page.getByLabel("エイリアス", { exact: true }).fill("test-model");
  await expect(
    page.getByText("接続先のモデルが1つのため、自動で設定します。"),
  ).toBeVisible();
  await expect(page.getByLabel("上流モデル名", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "保存する" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // Hidden from the menu; the page is still reachable by URL.
  await page.goto("/admin/users");
  await page.getByRole("button", { name: "利用者を登録" }).click();
  await page.getByLabel("利用者名", { exact: true }).fill("Test researcher");
  await page.getByRole("button", { name: "保存する" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "キー管理" }).click();
  await page.getByLabel("新しいキーの名前").fill("browser-test");
  await page.getByRole("button", { name: "発行", exact: true }).click();
  const key = await page.locator(".secret-result code").innerText();
  expect(key).toMatch(/^llm_router_/);
  await page.getByRole("button", { name: "閉じる", exact: true }).click();
  const response = await page.request.post("/v1/chat/completions", {
    headers: { Authorization: `Bearer ${key}` },
    data: {
      model: "test-model",
      messages: [{ role: "user", content: "test" }],
      stream: true,
    },
  });
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain("[DONE]");
  // Hidden from the menu; the page is still reachable by URL.
  await page.goto("/admin/requests");
  await expect(
    page.getByRole("cell", { name: "完了", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "詳細", exact: true }).click();
  await expect(
    page.getByRole("dialog").getByText("upstream-model", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "閉じる", exact: true }).click();
  // Hidden from the menu; the page is still reachable by URL.
  await page.goto("/admin/benchmarks");
  await page.getByRole("button", { name: "新しい負荷試験" }).click();
  await expect(page.getByLabel("入力データ", { exact: true })).toHaveValue(
    "huggingface",
  );
  await expect(
    page.getByLabel("公開データセット", { exact: true }),
  ).toHaveValue("openai/gsm8k");
  await expect(
    page.getByLabel("目標入力トークン数", { exact: true }),
  ).toHaveCount(0);
  await page
    .getByLabel("公開データセット", { exact: true })
    .selectOption("abisee/cnn_dailymail");
  await expect(page.getByLabel("入力本文の列名", { exact: true })).toHaveValue(
    "article",
  );
  await page
    .getByText("詳細な入力設定：合成入力・トレース再生", { exact: true })
    .click();
  await page
    .getByLabel("詳細な入力方式", { exact: true })
    .selectOption("trace");
  await expect(page.getByLabel("負荷方式", { exact: true })).toHaveValue(
    "replay",
  );
  await page
    .getByLabel("詳細な入力方式", { exact: true })
    .selectOption("synthetic");
  await expect(
    page.getByLabel("目標入力トークン数", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("入力データ", { exact: true }).selectOption("file");
  await expect(page.getByLabel("負荷方式", { exact: true })).toHaveValue(
    "concurrent",
  );
  await expect(page.getByLabel("入力本文の列名", { exact: true })).toHaveValue(
    "prompt",
  );
  await page.getByLabel("データを登録", { exact: true }).setInputFiles({
    name: "browser-fixture.jsonl",
    mimeType: "application/jsonl",
    buffer: Buffer.from(
      '{"prompt":"Explain a simple idea."}\n{"prompt":"Describe a short example."}',
    ),
  });
  await expect(
    page.getByLabel("データファイル", { exact: true }),
  ).not.toHaveValue("");
  await page.getByLabel("Tokenizer", { exact: true }).fill("gpt2");
  await page
    .getByRole("button", { name: "Tokenizerと入力長を確認", exact: true })
    .click();
  await expect(
    page.getByRole("columnheader", { name: "生成・読込後の入力tokens" }),
  ).toBeVisible({ timeout: 30000 });
  await page.getByLabel("各段階の要求数", { exact: true }).fill("2");
  await page.getByLabel("同時実行数の段階", { exact: true }).fill("1, 2");
  await page
    .getByLabel("この接続先で負荷試験を実施できることを確認しました")
    .check();
  await page.getByRole("button", { name: "試験を開始", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "GuideLLM試験", exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".stat-strip").getByText("完了", { exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByRole("cell", { name: "2 / 2", exact: true }),
  ).toHaveCount(2);
  await page.getByRole("link", { name: "接続先・Capacity設定へ →" }).click();
  await page.getByRole("button", { name: "同時実行数を変更" }).click();
  await page.getByLabel("Capacity", { exact: true }).fill("2");
  await page.getByRole("button", { name: "保存する" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.locator(".stat-strip").getByText("0 / 2", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "設定", exact: true }).click();
  await expect(
    page.getByText("期限なし（Streamingの無通信時間を含む）"),
  ).toBeVisible();
  await page.getByLabel("画面更新周期（秒）").fill("7");
  await page.getByRole("button", { name: "設定を保存" }).click();
  await expect(
    page.getByText("設定を保存しました", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "概況", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "test-endpoint", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".status-mark.success").first()).toHaveCSS(
    "background-color",
    "rgb(22, 128, 60)",
  );
  const colors = await page
    .locator("button.primary, .sidebar, .stat-strip")
    .evaluateAll((nodes) =>
      nodes.map((n) => getComputedStyle(n).backgroundColor),
    );
  for (const color of colors) {
    const channels = color.match(/[\d.]+/g)?.map(Number) || [];
    expect(channels[0]).toBe(channels[1]);
    expect(channels[1]).toBe(channels[2]);
  }
  await page.screenshot({
    path: "../.local/ui-test-overview.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "推論環境の概況" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "メニュー", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "利用者・キー", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("model form resets the mapping when switching endpoints", async ({
  page,
}) => {
  await page.goto("/admin");
  await page
    .getByLabel("パスワード", { exact: true })
    .fill("test-password-123");
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "推論環境の概況" }),
  ).toBeVisible();
  await page.route("**/api/admin/endpoints", async (route) => {
    await route.fulfill({
      json: [
        { id: "single", name: "Single", observed_models: [{ id: "one" }] },
        {
          id: "multi",
          name: "Multiple",
          observed_models: [{ id: "two" }, { id: "three" }],
        },
        { id: "unknown", name: "Unconfirmed", observed_models: [] },
      ],
    });
  });
  // Hidden from the menu; the page is still reachable by URL.
  await page.goto("/admin/aliases");
  await page.getByRole("button", { name: "エイリアスを登録" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("one", { exact: true })).toBeVisible();
  await dialog.getByLabel("接続先", { exact: true }).selectOption("multi");
  await expect(
    dialog.getByLabel("使用するモデル", { exact: true }),
  ).toHaveValue("");
  await dialog
    .getByLabel("使用するモデル", { exact: true })
    .selectOption("three");
  await dialog.getByLabel("接続先", { exact: true }).selectOption("unknown");
  await expect(dialog.getByLabel("上流モデル名", { exact: true })).toHaveValue(
    "",
  );
  await dialog.getByLabel("上流モデル名", { exact: true }).fill("manual-model");
  await dialog.getByLabel("接続先", { exact: true }).selectOption("single");
  await expect(dialog.getByText("one", { exact: true })).toBeVisible();
  await dialog.getByLabel("エイリアス", { exact: true }).fill("mapping-test");
  let saved: Record<string, unknown> | undefined;
  await page.route("**/api/admin/aliases", async (route) => {
    if (route.request().method() === "POST") {
      saved = route.request().postDataJSON();
      await route.fulfill({ status: 201, json: { id: "created" } });
    } else await route.continue();
  });
  await dialog.getByRole("button", { name: "保存する" }).click();
  await expect(dialog).toHaveCount(0);
  expect(saved).toMatchObject({ endpoint_id: "single", upstream_model: "one" });
});

test("organization branding persists on reload and login", async ({ page }) => {
  await page.goto("/admin");
  await page
    .getByLabel("パスワード", { exact: true })
    .fill("test-password-123");
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await page.getByRole("link", { name: "設定", exact: true }).click();
  await page.getByLabel("ロゴ文字", { exact: true }).fill("DEMO");
  await page.getByLabel("組織名", { exact: true }).fill("DEMO");
  await page
    .getByLabel("ワークスペースの説明", { exact: true })
    .fill("共用の推論基盤");
  await page.getByRole("button", { name: "設定を保存", exact: true }).click();
  await expect(page.locator(".sidebar .brand-symbol")).toHaveText("DEMO");
  await expect(page).toHaveTitle("DEMO — LLM Router");
  await page.reload();
  await expect(page.locator(".workspace-label")).toContainText("DEMO NETWORK");
  await expect(page.getByLabel("ロゴ文字", { exact: true })).toHaveValue("DEMO");
  await page.getByRole("button", { name: "ログアウト", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "推論環境の稼働状況" }),
  ).toBeVisible();
  await expect(page.locator(".public-layout .brand-symbol")).toHaveText("DEMO");
  await page
    .getByRole("button", { name: "管理者ログイン", exact: true })
    .click();
  await expect(page.locator(".login-aside .brand-symbol")).toHaveText("DEMO");
  await page.reload();
  await expect(page.locator(".login-aside .brand-symbol")).toHaveText("DEMO");
  await page.screenshot({ path: "../.local/branding-demo.png", fullPage: true });
  await page
    .getByLabel("パスワード", { exact: true })
    .fill("test-password-123");
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await page.getByRole("link", { name: "設定", exact: true }).click();
  await page.getByLabel("ロゴ文字", { exact: true }).fill("demo");
  await page.getByLabel("組織名", { exact: true }).fill("Demo");
  await page.getByRole("button", { name: "設定を保存", exact: true }).click();
  await expect(page.locator(".sidebar .brand-symbol")).toHaveText("demo");
  await expect(page).toHaveTitle("Demo — LLM Router");
});
