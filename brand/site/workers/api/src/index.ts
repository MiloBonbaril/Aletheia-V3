/**
 * API publique — Cloudflare Worker (stub).
 * GET /api/stream/status : statut du live, mis en cache dans KV.
 * À brancher sur Twitch (Helix /streams ou EventSub) : voir README.
 */
export interface Env {
  CACHE: KVNamespace;
  TWITCH_CLIENT_ID: string;
  TWITCH_CLIENT_SECRET: string;
  TWITCH_LOGIN: string;
  ALLOWED_ORIGIN: string;
}

const json = (data: unknown, env: Env, maxAge = 30) =>
  new Response(JSON.stringify(data), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `public, max-age=${maxAge}`,
      "access-control-allow-origin": env.ALLOWED_ORIGIN || "*",
    },
  });

async function twitchToken(env: Env): Promise<string> {
  const cached = await env.CACHE.get("twitch:token");
  if (cached) return cached;
  const res = await fetch("https://id.twitch.tv/oauth2/token", {
    method: "POST",
    body: new URLSearchParams({
      client_id: env.TWITCH_CLIENT_ID,
      client_secret: env.TWITCH_CLIENT_SECRET,
      grant_type: "client_credentials",
    }),
  });
  const { access_token, expires_in } = (await res.json()) as { access_token: string; expires_in: number };
  await env.CACHE.put("twitch:token", access_token, { expirationTtl: Math.max(60, expires_in - 300) });
  return access_token;
}

async function streamStatus(env: Env) {
  const cached = await env.CACHE.get("stream:status", "json");
  if (cached) return cached;
  const token = await twitchToken(env);
  const res = await fetch(`https://api.twitch.tv/helix/streams?user_login=${encodeURIComponent(env.TWITCH_LOGIN)}`, {
    headers: { "Client-Id": env.TWITCH_CLIENT_ID, Authorization: `Bearer ${token}` },
  });
  const { data } = (await res.json()) as { data: Array<{ title: string; viewer_count: number; started_at: string }> };
  const s = data[0];
  const status = s
    ? { live: true, platform: "twitch", title: s.title, viewers: s.viewer_count, startedAt: s.started_at }
    : { live: false };
  await env.CACHE.put("stream:status", JSON.stringify(status), { expirationTtl: 60 });
  return status;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const { pathname } = new URL(req.url);
    if (req.method !== "GET") return new Response("Method Not Allowed", { status: 405 });
    try {
      if (pathname === "/api/stream/status") return json(await streamStatus(env), env, 30);
      return new Response("Not Found", { status: 404 });
    } catch {
      return json({ live: false, error: "upstream" }, env, 10);
    }
  },
};
