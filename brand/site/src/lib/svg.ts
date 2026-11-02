/** Prépare un SVG maître pour l'inline : retire title/ids/dimensions fixes (évite les doublons d'id). */
export function inlineSvg(raw: string, className = "") {
  return raw
    .replace(/<\?xml[^>]*>/, "")
    .replace(/<title[^>]*>.*?<\/title>/s, "")
    .replace(/\s(width|height|role|aria-labelledby)="[^"]*"/g, "")
    .replace("<svg", `<svg class="${className}" aria-hidden="true" focusable="false"`);
}
