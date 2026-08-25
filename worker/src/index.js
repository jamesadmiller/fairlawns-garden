const ALLOWED_ORIGIN = 'https://jamesadmiller.github.io';
const NOTION_VERSION = '2022-06-28';

function withCors(resp) {
  resp.headers.set('Access-Control-Allow-Origin', ALLOWED_ORIGIN);
  resp.headers.set('Vary', 'Origin');
  return resp;
}

function json(body, status = 200) {
  return withCors(new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }));
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return withCors(new Response(null, {
        status: 204,
        headers: {
          'Access-Control-Allow-Methods': 'POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type, X-Site-Key',
        },
      }));
    }

    if (request.method !== 'POST') {
      return json({ ok: false, error: 'method not allowed' }, 405);
    }

    if (request.headers.get('X-Site-Key') !== env.SITE_KEY) {
      return json({ ok: false, error: 'unauthorized' }, 401);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ ok: false, error: 'invalid JSON body' }, 400);
    }

    const { pageId, done } = body || {};
    if (!pageId || typeof pageId !== 'string' || typeof done !== 'boolean') {
      return json({ ok: false, error: 'pageId (string) and done (boolean) are required' }, 400);
    }

    const notionResp = await fetch(`https://api.notion.com/v1/pages/${pageId}`, {
      method: 'PATCH',
      headers: {
        Authorization: `Bearer ${env.NOTION_TOKEN}`,
        'Notion-Version': NOTION_VERSION,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ properties: { Done: { checkbox: done } } }),
    });

    if (!notionResp.ok) {
      const detail = await notionResp.text();
      return json({ ok: false, error: `Notion API error (${notionResp.status})`, detail }, 502);
    }

    return json({ ok: true });
  },
};
