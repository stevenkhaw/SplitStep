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

`fence_model.json` is the fence-mount play model: a logistic regression's
feature list, standardisation, weights and segmentation constants. It is
written by `splitstep fence train` from the truth lists in
`tests/fixtures/truth/`, deterministically, so re-running the command
reproduces it; `tests/test_fence_train.py` fails if the two drift. Edit the
training, never the file. It is "fitted on one session" until a second
truth list exists (spec, Validation 3).
