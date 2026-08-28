# Bundled assets

`font.ttf` is **Roboto Condensed**, instanced to `wght=700` (Bold) from the
variable font Google Fonts ships. Licensed under the SIL Open Font License
1.1 — see https://github.com/google/fonts/blob/main/ofl/robotocondensed/OFL.txt

It is committed rather than fetched because it is the *only* way the app and
the CLI render a numbered reel identically. Before this, `overlay_font()`
found a bundled font inside the frozen app and fell through to macOS's Arial
Bold everywhere else, so the same reel burned in two different typefaces
depending on how it was rendered.

Instanced rather than shipped variable: `media/numbered.py` calls
`ImageFont.truetype` without selecting a variation, so a variable file would
silently render Regular.
