import { getCollection } from "astro:content";
import type { Slot } from "./schedule";

export async function getSlots(): Promise<Slot[]> {
  return (await getCollection("schedule")).map((e) => ({ ...e.data, id: e.id }));
}

export async function getLore() {
  return (await getCollection("lore", (e) => !e.data.draft)).sort((a, b) => a.data.chapter - b.data.chapter);
}
