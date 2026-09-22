# Solstead brand assets

The Solstead mark is a roof line over a half sun on the horizon: a home under the sun.

## Colors

| Name | Hex | Use |
| --- | --- | --- |
| Night | `#16222E` | App icon background, wordmark on light backgrounds |
| Ivory | `#F4EEE2` | Roof and horizon lines on the default icon; mark on dark backgrounds |
| Sun | `#F2A33A` | The sun, only. Too light for text on light backgrounds. |

## Files

| File | What it is |
| --- | --- |
| `solstead-icon.svg` | Master app icon, full-bleed square (iOS applies the rounded mask). |
| `solstead-icon-dark.svg` | Dark appearance: transparent background, the system supplies the dark backdrop. |
| `solstead-icon-tinted.svg` | Tinted appearance: grayscale on black, iOS applies the user's tint. |
| `solstead-mark.svg` | Glyph only, cropped, for light backgrounds (docs, website). |
| `solstead-mark-light.svg` | Glyph only, cropped, for dark backgrounds. |
| `solstead-icon-1024.png` | Raster preview of the master icon. |

The rendered 1024 × 1024 PNGs Xcode uses live in
`ios/Solstead/Assets.xcassets/AppIcon.appiconset/`. If you change an SVG, re-export those
three PNGs as well. The default PNG must be opaque (no alpha channel) for App Store submission.
