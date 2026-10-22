import { pathToFileURL } from "node:url";
import { startProdServer } from "vinext/server/prod-server";

// vinext 0.0.50 caches Windows relative paths with backslashes, while HTTP
// lookups use forward slashes. Adapt only the in-memory lookup; keep its
// existing path validation and static-file serving implementation.
if (process.platform === "win32") {
  const cacheModule = new URL("./static-file-cache.js", import.meta.resolve("vinext/server/prod-server"));
  const { StaticFileCache } = await import(cacheModule.href);
  const originalLookup = StaticFileCache.prototype.lookup;
  StaticFileCache.prototype.lookup = function (pathname) {
    return originalLookup.call(this, pathname) ?? originalLookup.call(
      this, "/" + pathname.slice(1).replaceAll("/", "\\"),
    );
  };
}

export function startWeb(options = {}) {
  return startProdServer(options);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const portIndex = process.argv.indexOf("--port");
  const port = Number(portIndex >= 0 ? process.argv[portIndex + 1] : process.env.PORT || 3000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid port");
  await startWeb({ port });
}
