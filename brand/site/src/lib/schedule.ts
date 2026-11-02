/**
 * Calculs de planning, sans dépendance.
 * Les créneaux sont saisis en heure de Paris ; l'affichage se fait dans le fuseau du visiteur.
 */
export type Slot = {
  id: string;
  weekday: number; // 1 = lundi … 7 = dimanche
  start: string; // "HH:MM", heure de référence
  durationMin: number;
  title: string;
  kind: "stream" | "special" | "discord";
  platform: "twitch" | "youtube" | "discord";
};

export type Occurrence = Slot & { startsAt: Date; endsAt: Date };

const WEEKDAYS: Record<string, number> = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 };

function partsIn(date: Date, timeZone: string) {
  const f = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    weekday: "short",
  });
  const p = Object.fromEntries(f.formatToParts(date).map((x) => [x.type, x.value]));
  return {
    y: +p.year,
    m: +p.month,
    d: +p.day,
    h: +p.hour,
    mi: +p.minute,
    s: +p.second,
    weekday: WEEKDAYS[p.weekday as string] ?? 1,
  };
}

/** Décalage (ms) du fuseau par rapport à UTC à l'instant donné. */
function offsetMs(date: Date, timeZone: string) {
  const p = partsIn(date, timeZone);
  return Date.UTC(p.y, p.m - 1, p.d, p.h, p.mi, p.s) - Math.floor(date.getTime() / 1000) * 1000;
}

/** Convertit une heure murale d'un fuseau en instant UTC. */
function zonedToDate(y: number, m: number, d: number, h: number, mi: number, timeZone: string) {
  const guess = Date.UTC(y, m - 1, d, h, mi);
  let t = guess - offsetMs(new Date(guess), timeZone);
  t = guess - offsetMs(new Date(t), timeZone); // 2e passe : changements d'heure
  return new Date(t);
}

/** Prochaine occurrence (ou occurrence en cours) de chaque créneau, triées. */
export function upcoming(slots: Slot[], now: Date, timeZone: string): Occurrence[] {
  const today = partsIn(now, timeZone);
  return slots
    .map((slot) => {
      const [h, mi] = slot.start.split(":").map(Number);
      const delta = (slot.weekday - today.weekday + 7) % 7;
      const base = new Date(Date.UTC(today.y, today.m - 1, today.d + delta));
      let startsAt = zonedToDate(base.getUTCFullYear(), base.getUTCMonth() + 1, base.getUTCDate(), h, mi, timeZone);
      let endsAt = new Date(startsAt.getTime() + slot.durationMin * 60_000);
      if (endsAt <= now) {
        const nb = new Date(base.getTime() + 7 * 86_400_000);
        startsAt = zonedToDate(nb.getUTCFullYear(), nb.getUTCMonth() + 1, nb.getUTCDate(), h, mi, timeZone);
        endsAt = new Date(startsAt.getTime() + slot.durationMin * 60_000);
      }
      return { ...slot, startsAt, endsAt };
    })
    .sort((a, b) => a.startsAt.getTime() - b.startsAt.getTime());
}

export function liveNow(slots: Slot[], now: Date, timeZone: string) {
  return upcoming(slots, now, timeZone).find(
    (o) => o.platform !== "discord" && o.startsAt <= now && now < o.endsAt,
  );
}

export const DAY_NAMES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];

export function formatTime(date: Date, timeZone?: string) {
  return new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone }).format(date);
}

export function formatDay(date: Date, timeZone?: string) {
  return new Intl.DateTimeFormat("fr-FR", { weekday: "long", timeZone }).format(date);
}

export function countdown(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return { d: Math.floor(s / 86400), h: Math.floor((s % 86400) / 3600), m: Math.floor((s % 3600) / 60), s: s % 60 };
}
