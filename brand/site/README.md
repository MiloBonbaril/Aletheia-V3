# Site public — Aletheia

Site vitrine de la VTubeuse IA **Aletheia**. Astro 7 (HTML statique) + îlots Svelte 5, CSS natif,
fond de particules OGL chargé en différé. Conforme à `../GUIDELINES.md` (logo, palette, Jost/Inter).

```bash
npm install
npm run dev       # http://localhost:4321
npm run build     # → dist/ (statique, prêt pour Cloudflare Pages)
```

## À remplacer avant la mise en ligne

| Quoi | Où |
|---|---|
| Liens Twitch / YouTube / Discord, nom de la communauté | `src/config.ts` |
| Domaine (`site`, canonical, OG) | `astro.config.mjs`, `SITE.twitchParents` |
| Planning hebdomadaire (heure de Paris) | `src/content/schedule.json` |
| Lore (brouillon à réécrire) | `src/content/lore/*.mdx` |
| URL de l'API | `.env` → `PUBLIC_API_BASE` (vide = statut simulé depuis le planning) |

## Structure

```text
src/
├── assets/            aletheia.png (illustration détourée), brand/ (SVG maîtres)
├── components/
│   ├── astro/         Header, Footer, Icon, TwitchEmbed, home/*
│   └── svelte/        LiveStatus (client:idle), Schedule (client:visible / load)
├── content/           lore/*.mdx, schedule.json  (Content Layer, src/content.config.ts)
├── layouts/Base.astro head-snippet de brand/export intégré
├── lib/               schedule.ts (fuseaux sans dépendance), api.ts, svg.ts
├── pages/             /, /qui-est-elle, /lore, /lore/[slug], /planning, /planning.ics, 404
├── scripts/field.ts   fond OGL (import dynamique)
└── styles/global.css  design tokens, motif « ruban », animations
workers/api/           Cloudflare Worker GET /api/stream/status (Twitch Helix + KV) — stub
```

## Choix de performance

- Zéro JS pour le contenu ; deux îlots Svelte (~20 Ko gzip au total avec le runtime).
- OGL (~14 Ko gzip) chargé seulement en `requestIdleCallback`, jamais si `prefers-reduced-motion`,
  `Save-Data` ou écran < 640 px ; rendu stoppé hors écran / onglet masqué, DPR ≤ 1,5.
- Transitions entre pages : View Transitions **cross-document** en CSS (`@view-transition`), sans routeur JS.
- Animations au défilement : `animation-timeline: view()/scroll()` en amélioration progressive.
- Menu mobile : Popover API native. FAQ : `<details name>` natif animé.
- Illustration servie en AVIF/WebP responsive ; lecteur Twitch en façade (chargé au clic).

## Notes de marque

- Blé n'est jamais utilisé en texte sur Papier (2.0:1) : uniquement en aplats.
- Sur Casaque, le texte est en Papier (jamais en Encre).
- Le motif « casaque » (rayures du ruban) est la variable CSS `--ruban` (item ouvert de GUIDELINES §9).
- Logo d'en-tête à 168 px (minimum 160 px).

## API (Cloudflare Worker)

```bash
cd workers/api
npx wrangler kv namespace create CACHE      # reporter l'id dans wrangler.toml
npx wrangler secret put TWITCH_CLIENT_ID
npx wrangler secret put TWITCH_CLIENT_SECRET
npx wrangler deploy
```
