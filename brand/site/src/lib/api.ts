/**
 * Client des endpoints publics (Cloudflare Workers).
 * Sans PUBLIC_API_BASE, les données sont simulées à partir du planning.
 */
import { liveNow, type Slot } from "./schedule";

export type StreamStatus = {
  live: boolean;
  title?: string;
  platform?: "twitch" | "youtube";
  viewers?: number;
  startedAt?: string;
  source: "api" | "simulated";
};

export async function fetchStreamStatus(
  apiBase: string | undefined,
  slots: Slot[],
  timeZone: string,
  signal?: AbortSignal,
): Promise<StreamStatus> {
  if (apiBase) {
    try {
      const res = await fetch(`${apiBase.replace(/\/$/, "")}/api/stream/status`, {
        signal,
        headers: { accept: "application/json" },
      });
      if (res.ok) return { ...(await res.json()), source: "api" };
    } catch {
      /* repli sur la simulation */
    }
  }
  const now = new Date();
  const current = liveNow(slots, now, timeZone);
  return current
    ? {
        live: true,
        title: current.title,
        platform: current.platform as "twitch" | "youtube",
        startedAt: current.startsAt.toISOString(),
        source: "simulated",
      }
    : { live: false, source: "simulated" };
}
