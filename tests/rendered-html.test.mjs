import assert from "node:assert/strict";
import test from "node:test";
import { startWeb } from "../scripts/start-web.mjs";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the HydroBridge application shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html[^>]*lang="zh-CN"/i);
  assert.match(html, /HydroBridge AI/);
  assert.match(html, /水利科技智能决策平台/);
  assert.match(html, /正在连接 HydroBridge AI/);
  assert.match(html, /HydroBridgeApp-[^"]+\.js/);
  assert.doesNotMatch(html, /Your site is taking shape|Codex is building/);
});

test("production HTTP server serves scripts and CSS needed for hydration", async () => {
  const { server, port } = await startWeb({ port: 0, host: "127.0.0.1" });
  const base = `http://127.0.0.1:${port}`;
  try {
    const html = await (await fetch(base)).text();
    const assets = [...new Set(html.match(/\/assets\/[A-Za-z0-9_-]+\.(?:js|css)/g))];
    assert.ok(assets.some(path => path.endsWith(".js")));
    assert.ok(assets.some(path => path.endsWith(".css")));
    for (const asset of assets) {
      const response = await fetch(base + asset);
      assert.equal(response.status, 200, asset);
      assert.match(response.headers.get("content-type") || "", /javascript|css/);
      assert.ok((await response.text()).length > 0);
    }
    assert.equal((await fetch(base + "/.vite/manifest.json")).status, 404);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
