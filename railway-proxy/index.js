// Google Play proxy for Railway
// Deploys as an HTTP server that fetches Google Play Store pages
// and returns the HTML, bypassing GFW restrictions.
//
// Usage:
//   GET /proxy?url=https://play.google.com/store/apps/category/GAME/collection/topselling_free&gl=US
//
// Deploy:
//   railway login --token <token>
//   railway up
//   railway domain

const http = require('http');
const url = require('url');

const TARGET_DOMAINS = [
  'play.google.com',
  'play-lh.googleusercontent.com',
  'lh3.googleusercontent.com',
];

const PORT = process.env.PORT || 3000;

const server = http.createServer(async (req, res) => {
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const parsed = url.parse(req.url, true);
  const targetUrl = parsed.query.url;

  // Health check
  if (parsed.pathname === '/' || parsed.pathname === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', service: 'google-play-proxy' }));
    return;
  }

  if (parsed.pathname !== '/proxy') {
    res.writeHead(404);
    res.end(JSON.stringify({ error: 'Not found. Use /proxy?url=<encoded-url>' }));
    return;
  }

  if (!targetUrl) {
    res.writeHead(400);
    res.end(JSON.stringify({ error: 'Missing "url" query parameter' }));
    return;
  }

  // Security: only allow Google Play related URLs
  try {
    const parsedTarget = new URL(targetUrl);
    if (!TARGET_DOMAINS.some(d => parsedTarget.hostname.endsWith(d))) {
      res.writeHead(403);
      res.end(JSON.stringify({
        error: `Domain not allowed: ${parsedTarget.hostname}`,
        allowed: TARGET_DOMAINS,
      }));
      return;
    }
  } catch {
    res.writeHead(400);
    res.end(JSON.stringify({ error: 'Invalid URL' }));
    return;
  }

  try {
    const response = await fetch(targetUrl, {
      headers: {
        'User-Agent':
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ' +
          'AppleWebKit/537.36 (KHTML, like Gecko) ' +
          'Chrome/120.0.0.0 Safari/537.36',
        Accept:
          'text/html,application/xhtml+xml,application/xml;q=0.9,' +
          'image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
      },
      redirect: 'follow',
    });

    const html = await response.text();
    const contentType = response.headers.get('content-type') || 'text/html; charset=utf-8';

    res.setHeader('Content-Type', contentType);
    res.setHeader('Cache-Control', 'public, max-age=300, s-maxage=300');
    res.writeHead(response.status);
    res.end(html);
  } catch (error) {
    res.writeHead(502);
    res.end(JSON.stringify({
      error: 'Failed to fetch from Google Play',
      detail: error.message,
    }));
  }
});

server.listen(PORT, () => {
  console.log(`Google Play proxy running on port ${PORT}`);
});
