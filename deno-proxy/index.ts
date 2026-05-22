// Google Play proxy for Deno Deploy
// Deployed at https://google-play-proxy.deno.dev
//
// Usage:
//   /proxy?url=https://play.google.com/store/apps/category/GAME/collection/topselling_free?gl=US
//
// Deploy:
//   deployctl deploy --project=google-play-proxy index.ts

const TARGET_DOMAINS = [
  "play.google.com",
  "play-lh.googleusercontent.com",
  "lh3.googleusercontent.com",
];

Deno.serve(async (req: Request) => {
  const url = new URL(req.url);
  const targetUrl = url.searchParams.get("url");

  // CORS preflight
  if (req.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
      },
    });
  }

  // Health check
  if (url.pathname === "/" || url.pathname === "/health") {
    return new Response(JSON.stringify({ status: "ok", service: "google-play-proxy" }), {
      headers: { "content-type": "application/json" },
    });
  }

  if (url.pathname !== "/proxy") {
    return new Response(
      JSON.stringify({ error: 'Not found. Use /proxy?url=<encoded-url>' }),
      { status: 404, headers: { "content-type": "application/json" } },
    );
  }

  if (!targetUrl) {
    return new Response(
      JSON.stringify({ error: 'Missing "url" query parameter' }),
      { status: 400, headers: { "content-type": "application/json" } },
    );
  }

  // Security: only allow Google Play related URLs
  try {
    const parsedTarget = new URL(targetUrl);
    if (!TARGET_DOMAINS.some((d) => parsedTarget.hostname.endsWith(d))) {
      return new Response(
        JSON.stringify({
          error: `Domain not allowed: ${parsedTarget.hostname}`,
          allowed: TARGET_DOMAINS,
        }),
        { status: 403, headers: { "content-type": "application/json" } },
      );
    }
  } catch {
    return new Response(
      JSON.stringify({ error: "Invalid URL" }),
      { status: 400, headers: { "content-type": "application/json" } },
    );
  }

  try {
    const response = await fetch(targetUrl, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
          "AppleWebKit/537.36 (KHTML, like Gecko) " +
          "Chrome/120.0.0.0 Safari/537.36",
        Accept:
          "text/html,application/xhtml+xml,application/xml;q=0.9," +
          "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
      },
      redirect: "follow",
    });

    const html = await response.text();
    const contentType = response.headers.get("content-type") || "text/html; charset=utf-8";

    return new Response(html, {
      status: response.status,
      headers: {
        "content-type": contentType,
        "access-control-allow-origin": "*",
        "cache-control": "public, max-age=300, s-maxage=300",
      },
    });
  } catch (error) {
    return new Response(
      JSON.stringify({
        error: "Failed to fetch from Google Play",
        detail: error instanceof Error ? error.message : String(error),
      }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }
});
