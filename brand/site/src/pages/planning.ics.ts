import type { APIRoute } from "astro";
import { SITE, LINKS } from "../config";
import { getSlots } from "../lib/content";

/** Agenda iCalendar statique, événements hebdomadaires récurrents (heure de Paris). */
const DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];

export const GET: APIRoute = async () => {
  const slots = await getSlots();
  // Lundi de référence : 5 janvier 2026
  const ref = new Date(Date.UTC(2026, 0, 5));
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const fmt = (d: Date, hm: string) =>
    `${d.toISOString().slice(0, 10).replace(/-/g, "")}T${hm.replace(":", "")}00`;

  const events = slots.map((s) => {
    const day = new Date(ref.getTime() + (s.weekday - 1) * 86_400_000);
    const [h, m] = s.start.split(":").map(Number);
    const endMin = h * 60 + m + s.durationMin;
    const endDay = new Date(day.getTime() + Math.floor(endMin / 1440) * 86_400_000);
    const endHm = `${String(Math.floor((endMin % 1440) / 60)).padStart(2, "0")}:${String(endMin % 60).padStart(2, "0")}`;
    const url = LINKS[s.platform];
    return [
      "BEGIN:VEVENT",
      `UID:${s.id}@aletheia`,
      `DTSTAMP:${stamp}`,
      `DTSTART;TZID=${SITE.timezone}:${fmt(day, s.start)}`,
      `DTEND;TZID=${SITE.timezone}:${fmt(endDay, endHm)}`,
      `RRULE:FREQ=WEEKLY;BYDAY=${DAYS[s.weekday - 1]}`,
      `SUMMARY:${SITE.name} — ${s.title}`,
      `URL:${url}`,
      `DESCRIPTION:${url}`,
      "END:VEVENT",
    ].join("\r\n");
  });

  const body = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Aletheia//Planning//FR",
    "CALSCALE:GREGORIAN",
    `X-WR-CALNAME:${SITE.name} — streams`,
    `X-WR-TIMEZONE:${SITE.timezone}`,
    ...events,
    "END:VCALENDAR",
    "",
  ].join("\r\n");

  return new Response(body, { headers: { "Content-Type": "text/calendar; charset=utf-8" } });
};
