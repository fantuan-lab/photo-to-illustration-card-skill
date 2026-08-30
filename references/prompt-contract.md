# Prompt contract

Read this reference when generating the illustration layer or when the user asks for a reusable prompt.

## Build the subject lock first

Describe only visible, task-relevant facts. Keep the lock stable across iterations.

```text
Subject lock:
- count/category: <one orange cat / one person / one ceramic mug>
- identity anchors: <face shape, color blocks, markings, clothing, object details>
- pose and view: <lying down, front three-quarter view, gaze direction>
- silhouette and proportions: <round body, short ears, long handle>
- required crop-safe features: <both ears, front paws, entire handle>
- must not appear: <extra subjects, limbs, accessories, text, logo>
```

Do not add a breed, gender, name, brand, mood diagnosis, or sensitive personal attribute unless the user supplied it.

## Transparent illustration-layer prompt

Use this for the default two-stage workflow. Replace only the bracketed parts that are relevant.

```text
Use case: style-transfer
Asset type: transparent illustrated subject layer for a photo comparison card
Input images: Image 1 is the source photo and identity/pose reference
Primary request: draw only the main subject from Image 1 as a simplified hand-drawn illustration
Subject lock: [stable subject lock]
Style/medium: [selected preset]; flat simplified shapes; handmade linework
Composition/framing: preserve the source pose, viewpoint, silhouette, and relative proportions; keep the complete subject visible with comfortable transparent padding
Color palette: preserve the subject's identifying colors and markings; use low-saturation supporting color only where the preset calls for it
Background: genuinely transparent alpha; no paper, rectangle, scene, border, or drop shadow
Text: none
Constraints: the result must be immediately recognizable as the same subject; preserve count, face, markings, pose, expression, clothing, and required objects; clean usable edges
Avoid: extra subjects or body parts; changed markings; photorealistic rendering; 3D volume; heavy shadows; gradients; decorative props; words; logos; watermark
```

If alpha is not clean, request one targeted edit: change only the background to genuine transparency, keep every subject pixel and identifying feature unchanged. Inspect the saved file rather than trusting a checkerboard preview. If the result is still RGB or has no transparent range, stop retrying and use the opaque panel fallback below. Do not chroma-key a background color that also appears in the subject.

## Opaque text-free panel fallback

Use this after a failed alpha correction or when the user wants the image model to control the paper and accent texture. The composer treats the generated image as the entire lower region and adds exact text afterward.

```text
Use case: style-transfer
Asset type: text-free lower illustration panel for a 3:4 photo comparison card
Input images: Image 1 is the source photo and identity/pose reference
Primary request: create only the lower illustration panel, showing a simplified hand-drawn version of the main subject from Image 1
Subject lock: [stable subject lock]
Scene/backdrop: warm off-white handmade paper with restrained fibers and faint aged grain; one irregular low-saturation accent field derived from the source photo
Style/medium: [selected preset]
Composition/framing: landscape panel matching the selected layout; preserve the complete subject; leave generous clean negative space for later typography
Text: none
Constraints: immediately recognizable as the same subject; preserve count, face, markings, pose, expression, clothing, and required objects; flat handmade illustration
Avoid: transparency or checkerboard; extra subjects or anatomy; words, letters, numbers, logos, watermark; photorealistic rendering; 3D volume; heavy shadows; gradients; dense decoration
```

Do not attempt automatic background removal on the opaque panel. Pass it to the composer with illustration mode `panel` or `auto`.

## Card copy contract

The image model does not typeset card copy. Pass exact strings to the composer.

- `title`: 2–4 words, usually uppercase, memorable at thumbnail size.
- `subtitle`: 1–3 words, factual or playful, optional.
- `callout-left` and `callout-right`: 1–4 words each, optional.
- `caption`: 3–7 words, a gentle situational line, optional.

Match the user's requested language. When unspecified and the request points to the reference postcard look, concise English copy is acceptable. Avoid pretending to know a subject's name, breed, personality, ownership, or backstory.

## Unified prompt-only approximation

Use this only when the user asks for a paste-ready one-shot prompt or explicitly accepts a less reliable composite.

```text
Use case: style-transfer
Asset type: 3:4 vertical photo-to-illustration postcard
Input images: Image 1 is the source photo
Primary request: make a two-region comparison card from Image 1
Layout: [balanced: equal upper and lower regions / postcard: upper photo 70%, lower illustration 30%]
Upper region: use the original source photo as-is; allow only crop and scale; do not repaint, relight, beautify, or restyle it
Lower region: warm off-white handmade paper with restrained fibers and subtle aged grain; one irregular low-saturation accent field sampled from the source photo; generous negative space
Illustration: simplify the same subject into a recognizable [preset] drawing while preserving [subject lock]
Text (verbatim): title "[TITLE]"; subtitle "[SUBTITLE]"; left callout "[LEFT]"; right callout "[RIGHT]"; caption "[CAPTION]"
Typography: loose hand-lettered display title with small supporting labels, all readable and correctly spelled
Constraints: exact 3:4 portrait; no extra subjects or objects; no logos; no watermark; no heavy shadows; no realistic painted lower panel; no extra words
```

Warn that a one-shot image model may alter the upper photo or misspell copy. Prefer the transparent-layer plus deterministic composer for final output.
