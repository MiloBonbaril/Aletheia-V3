// @ts-check
import { defineConfig } from "astro/config";
import svelte from "@astrojs/svelte";
import mdx from "@astrojs/mdx";

export default defineConfig({
  site: "https://aletheia.example", // TODO: domaine définitif
  integrations: [svelte(), mdx()],
  prefetch: { prefetchAll: false, defaultStrategy: "hover" },
  build: { inlineStylesheets: "auto" },
});
