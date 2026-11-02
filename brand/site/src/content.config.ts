import { defineCollection } from "astro:content";
import { glob, file } from "astro/loaders";
import { z } from "astro/zod";

const lore = defineCollection({
  loader: glob({ pattern: "**/*.{md,mdx}", base: "./src/content/lore" }),
  schema: z.object({
    chapter: z.number().int().positive(),
    title: z.string(),
    /** Repère temporel dans l'histoire (texte libre). */
    era: z.string(),
    summary: z.string(),
    draft: z.boolean().default(false),
  }),
});

const schedule = defineCollection({
  loader: file("./src/content/schedule.json"),
  schema: z.object({
    /** 1 = lundi … 7 = dimanche */
    weekday: z.number().int().min(1).max(7),
    /** Heure de début, heure de Paris, "HH:MM" */
    start: z.string().regex(/^\d{2}:\d{2}$/),
    durationMin: z.number().int().positive(),
    title: z.string(),
    kind: z.enum(["stream", "special", "discord"]),
    platform: z.enum(["twitch", "youtube", "discord"]),
  }),
});

export const collections = { lore, schedule };
