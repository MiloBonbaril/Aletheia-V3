/**
 * Configuration centrale du site.
 * Tous les liens externes sont ici : remplace les placeholders avant la mise en ligne.
 */
export const SITE = {
  name: "Aletheia",
  tagline: "VTubeuse IA francophone",
  description:
    "Aletheia est une VTubeuse IA francophone : elle écoute, voit, se souvient et répond en direct sur Twitch, YouTube et Discord.",
  locale: "fr-FR",
  /** Fuseau horaire de référence du planning. */
  timezone: "Europe/Paris",
  /** Nom de la communauté (placeholder). */
  community: "le Paddock",
  /** Domaines autorisés pour l'embed Twitch (paramètre `parent`). */
  twitchParents: ["aletheia.example", "localhost"],
} as const;

export const LINKS = {
  twitch: "https://www.twitch.tv/aletheia_placeholder",
  twitchChannel: "aletheia_placeholder",
  youtube: "https://www.youtube.com/@aletheia_placeholder",
  discord: "https://discord.gg/placeholder",
} as const;

export const NAV = [
  { href: "/#stream", label: "Le stream" },
  { href: "/qui-est-elle", label: "Qui est-elle ?" },
  { href: "/lore", label: "Lore" },
  { href: "/planning", label: "Planning" },
] as const;

/** Les cinq adjectifs de la plateforme de marque. */
export const TRAITS = ["rayonnante", "curieuse", "espiègle", "vive", "lucide"] as const;
