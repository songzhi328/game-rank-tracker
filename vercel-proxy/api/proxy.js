// Google Play proxy for Vercel
// Deploys as a serverless function that fetches Google Play Store pages
// and returns the HTML, bypassing GFW restrictions.
//
// Usage:
//   /api/proxy?url=https://play.google.com/store/apps/category/GAME/collection/topselling_free&gl=us
//
// Deploy:
//   npx vercel deploy --prod

const TARGET_DOMAINS = [
  'play.google.com',
  'play-lh.googleusercontent.com',
  'lh3.googleusercontent.com',
];

export default async function handler(req, res) {
  const { url } = req.query;

  if (!url) {
    return res.status(400).json({ error: 'Missing "url" query parameter' });
  }

  // Security: only allow Google Play related URLs
  try {
    const parsed = new URL(url);
    if (!TARGET_DOMAINS.some(d => parsed.hostname.endsWith(d))) {
      return res.status(403).json({
        error: `Domain not allowed: ${parsed.hostname}`,
        allowed: TARGET_DOMAINS,
      });
    }
  } catch {
    return res.status(400).json({ error: 'Invalid URL' });
  }

  try {
    const response = await fetch(url, {
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

    // Important: Google Play rankings are server-side rendered.
    // The initial HTML contains the full app list with package names,
    // ranks, titles, ratings, etc. Client-side JS is not needed for data extraction.

    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Content-Type', contentType);
    res.setHeader('Cache-Control', 'public, max-age=300, s-maxage=300');
    res.status(response.status).send(html);
  } catch (error) {
    res.status(502).json({
      error: 'Failed to fetch from Google Play',
      detail: error.message,
    });
  }
}
