# Aletheia — logo guidelines

This file tells you how to use the Aletheia logo. Read it before you put the logo on a page, a
stream overlay or a product.

## 1. The logo

**Idea:** two horse ears lean towards each other and make the letter A. The round glasses make the
crossbar. The ears and the glasses are the two signs of the character. They are also two of her
senses: she hears and she sees.

The logo has four versions:

| Version | Use it for | Master file |
|---|---|---|
| Horizontal (primary) | The site header, banners, documents. The symbol is the capital A of "Aletheia". | `logo/aletheia-horizontal.svg` |
| Stacked | Square areas: posters, merchandise, the end screen of a stream. | `logo/aletheia-stacked.svg` |
| Symbol | Avatars, the app icon, stickers, emotes. | `logo/aletheia-symbol.svg` |
| Wordmark | Areas that already show the symbol near the name. | `logo/aletheia-wordmark.svg` |

Each version has a reversed file (`*-reversed.svg`) for dark backgrounds. The reversed files have
lighter strokes, because light shapes on a dark background look heavier. Use them, not a recoloured
copy of the dark file.

`logo/aletheia-symbol-small.svg` is a special drawing for small sizes. It has larger lenses, wider
gaps and a thicker bridge.

## 2. Clear space

The unit of clear space is **L**, the outer diameter of one lens in the logo you use. Keep a clear
zone of 1 L on all sides of the logo. Do not put text, images or edges in this zone. The zone
changes with the size of the logo.

## 3. Minimum size

| Version | Screen | Print |
|---|---|---|
| Horizontal | 160 px wide | 35 mm wide |
| Stacked | 96 px wide | 22 mm wide |
| Symbol | 32 px | 8 mm |
| Symbol, small-size drawing | 16 px | 5 mm |

Below 32 px, use the small-size drawing. The favicon files already use it.

## 4. Colour

| Name | Role | HEX | RGB | CMYK (start value) | Pantone (approximate) |
|---|---|---|---|---|---|
| Encre (ink) | The logo, the text | `#1E1B24` | 30 27 36 | 17 25 0 86 | Black 6 C |
| Blé (wheat) | Signature colour: avatar, icon, large areas | `#E6A752` | 230 167 82 | 0 27 64 10 | 7563 C or 1365 C |
| Casaque (silk) | Accent: links, buttons, one-colour logo | `#1F5A48` | 31 90 72 | 66 0 20 65 | 343 C |
| Papier (paper) | Background | `#FFF7EC` | 255 247 236 | 0 3 7 0 | — |

The CMYK and Pantone values are approximations. Get a printed proof before a print run, and compare
the Pantone values with a physical colour guide.

**Approved logo and background pairs** (WCAG contrast ratio):

| Logo | Background | Contrast |
|---|---|---|
| Encre | Papier or white | 16.0 : 1 |
| Encre | Blé | 8.1 : 1 |
| Papier (reversed file) | Encre | 16.0 : 1 |
| Blé (`export/*-reversed-e6a752.svg`) | Encre | 8.1 : 1 |
| Papier (reversed file) | Casaque | 7.6 : 1 |
| Casaque (`*-mono-1f5a48`) | Papier or white | 7.6 : 1 |

Do not put the logo in Blé on Papier or on white (2.0 : 1). Do not put the logo in Encre on
Casaque (2.1 : 1).

The logo always has one colour. Do not use gradients, shadows or outlines.

## 5. Typography

The wordmark is not a font. Its letters are drawn as paths. Do not type "aletheia" in a font to
make the logo.

For the site and the overlays, use two families:

- Headings: **Jost**, weight 500 or 600. Its geometric shapes agree with the wordmark.
- Body text: **Inter**, weights 400 and 600.
- Fallback: `system-ui, sans-serif`.

The two families are on Google Fonts, under the SIL Open Font License 1.1.

## 6. Incorrect uses

- Do not stretch, squash, rotate or tilt the logo.
- Do not change the colours to colours that are not in the palette.
- Do not add shadows, outlines, glows or gradients.
- Do not move or resize the parts of a lockup.
- Do not put the logo on a busy image. Put it on a calm area, or in a Blé or Encre container.
- Do not use the regular symbol below 32 px. Use the small-size drawing.

## 7. Files

| Folder | Contents |
|---|---|
| `logo/` | The SVG masters, with the reversed versions and the small-size symbol. |
| `export/` | Black, Casaque and Blé one-colour SVG files, PNG files (512 px to 1200 px), the app icon, the favicon files, `site.webmanifest` and `head-snippet.html`. |
| `presentation/` | `board.html`: the logo on a social profile, a site, a browser tab, a sticker, a T-shirt and an app icon. `kit-overview.png`: the logos and the palette on one page. |
| `concepts/` | Round 1: the three first concepts. Concept A (the head) can become the mascot for emotes. |
| `src/` | The script that builds the masters. |

To put the icons on a site, copy these files of `export/` to the root of the site: `favicon.ico`,
`favicon.svg`, `apple-touch-icon.png`, `site.webmanifest`, `icon-192.png`, `icon-512.png` and
`maskable-512.png`. Then copy the content of `head-snippet.html` into the `<head>` of the pages.

`export/favicon.svg` becomes Blé when the browser uses a dark theme. The `.ico` and PNG favicon
files have a Blé tile, thus they show on light and dark tabs.

## 8. Rebuild the files

The masters come from `src/build.py`. Do not edit the SVG masters by hand: change the values in
`build.py`, then run these commands:

```bash
cd brand/src
pip install -r requirements.txt
python build.py
```

The script writes `logo/` and the two favicon SVG files of `export/`. The other files of `export/`
come from the `logo-design` skill (`export_variants.py`). If you run `export_variants.py` again,
run `build.py` again after it, because `export_variants.py` replaces `favicon.svg`. Then build the
`.ico` file and the favicon PNG files from `favicon-tile.svg`.

## 9. Open items

- Trademark search. Nobody did a search. Do one before the public launch of the site.
- Pantone values. Compare them with a physical colour guide.
- The dark-theme `favicon.svg` is not tested in a real browser.
- Emotes. Make a mascot from concept A (`concepts/`).
- Pattern. Make a striped pattern from the ribbon (Casaque, Papier, Encre) for the site and the
  stream overlays.
