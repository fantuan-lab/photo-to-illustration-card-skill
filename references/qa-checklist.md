# QA checklist

Use both deterministic checks and visual review. Passing file checks alone does not prove that the subject remained recognizable.

## Deterministic checks

- The PNG opens and has the requested dimensions.
- The canvas is exactly 3:4 unless the user explicitly chose another ratio.
- The photo/illustration split matches `balanced` or `postcard` within 1%.
- The upper region comes from the source photo after only EXIF correction, crop or contain placement, and resize.
- The output file is not an input path and an existing output was not silently overwritten.
- The JSON sidecar records input hashes, output dimensions, layout, crop mode and focus, palette, seed, copy, and font choice.
- The sidecar records whether the lower visual used `cutout` or `panel`; `cutout` must have a real alpha channel with both transparent and opaque pixels.
- Exact copy matches the requested strings; no field crosses the safe margins.

Run:

```bash
python3 scripts/check_output.py --image <card.png> --sidecar <card.png.json>
```

Use the actual sidecar filename written by `compose_card.py` when it differs.

## Full-size visual review

- The source photo is still visibly photographic and has not been repainted, beautified, relit, or restyled.
- No required head, ear, hand, foot, tail, prop, product edge, or landmark is clipped.
- The illustration is immediately recognizable as the same subject.
- Subject count, anatomy, face, expression, pose, gaze, silhouette, proportions, colors, markings, clothing, and key objects are correct.
- In cutout mode, transparent edges are clean, with no opaque box, baked checkerboard, color halo, accidental crop, or cut-off stroke. In panel mode, the opaque lower visual fills its region cleanly and contains no generated text.
- Paper and grain are visible but restrained; the accent color coordinates with the photo.
- The lower panel remains flat and handmade, without realistic rendering, heavy shadows, gradients, or unrelated decorations.

## Thumbnail review

Inspect at approximately phone-feed width:

- title and subject remain legible;
- illustration silhouette is clear;
- photo and illustration read as a matched pair;
- the card does not feel crowded;
- supporting labels do not become distracting specks.

## Correction strategy

- Wrong text, spacing, crop, color block, texture, or split: rerun only the composer.
- A side callout overlaps the subject in panel mode: rerun the composer with `--callout-left-y <0..1>` or `--callout-right-y <0..1>`; do not regenerate the illustration just to move a label.
- Wrong subject identity, pose, anatomy, markings, or transparency: make one targeted image-generation edit while repeating the stable subject lock, then recompose.
- Wrong source photo: stop and resolve the intended input; never guess from a collage or screenshot.
- Repeated identity failure: keep the best source-safe composite, explain the failed invariant, and ask for a clearer source photo or a user choice before further costly attempts.
