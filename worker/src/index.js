const ALLOWED_ORIGIN = 'https://jamesadmiller.github.io';
const NOTION_VERSION = '2022-06-28';

// Only these checkbox properties can be written, keeping the blast radius of
// a leaked SITE_KEY bounded to "flip these specific known checkboxes" rather
// than "write any property on any page in the workspace".
const ALLOWED_FIELDS = { done: 'Done', toBuy: 'To Buy' };

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

    const { pageId, field, value } = body || {};
    const notionProperty = ALLOWED_FIELDS[field];
    if (!pageId || typeof pageId !== 'string' || !notionProperty || typeof value !== 'boolean') {
      return json({ ok: false, error: `pageId (string), field (one of ${Object.keys(ALLOWED_FIELDS).join(', ')}), and value (boolean) are required` }, 400);
    }

    const notionResp = await fetch(`https://api.notion.com/v1/pages/${pageId}`, {
      method: 'PATCH',
      headers: {
        Authorization: `Bearer ${env.NOTION_TOKEN}`,
        'Notion-Version': NOTION_VERSION,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ properties: { [notionProperty]: { checkbox: value } } }),
    });

    if (!notionResp.ok) {
      const detail = await notionResp.text();
      return json({ ok: false, error: `Notion API error (${notionResp.status})`, detail }, 502);
    }

    return json({ ok: true });
  },
};
