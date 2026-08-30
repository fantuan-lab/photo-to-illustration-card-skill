# Style presets

Use material and rendering terms instead of imitating a named living artist. The source photo controls subject identity; a preset controls only the illustrated treatment and lower-panel mood.

## `crayon-riso` — default

- Line: loose dark wax-pencil contour, slightly uneven and naive.
- Fill: simplified flat shapes that preserve identifying colors and markings.
- Card panel: warm off-white coarse paper, subtle age, fine risograph-like grain.
- Accent: one irregular muted color field sampled from the photo.
- Mood: quiet, warm, retro, unforced.
- Avoid: polished vector geometry, glossy 3D, heavy shading, dense decoration.

Use for the reference look, pets, casual portraits, travel objects, food, and small everyday scenes.

## `bold-doodle`

- Line: thicker black marker contour with energetic imperfections.
- Fill: two to four flat colors with stronger contrast.
- Card panel: cream paper with light ink speckle.
- Accent: bolder but still subject-derived.
- Mood: playful, graphic, humorous.
- Avoid: tiny detail, complex texture, realism, gradients.

Use when the user wants a louder social-thumbnail result without turning the task into an article carousel.

## `soft-editorial`

- Line: soft graphite or colored-pencil contour.
- Fill: restrained editorial washes and quiet negative space.
- Card panel: smooth warm paper with very subtle grain.
- Accent: pale desaturated block or underline.
- Mood: calm, refined, magazine-like.
- Avoid: cartoon exaggeration, high saturation, rough marker weight.

Use for people, interiors, products, and understated keepsakes.

## `storybook`

- Line: friendly ink-and-gouache treatment with rounded simplification.
- Fill: gentle opaque color, limited texture, no dramatic modeling.
- Card panel: warm illustrated-book paper.
- Accent: one soft scene-derived color.
- Mood: tender and narrative.
- Avoid: changing age, identity, pose, anatomy, or adding story props that are absent from the request.

Use when the user explicitly asks for a children's-book feeling. It is not permission to redesign the subject.

## Layout presets

- `balanced`: 3:4 portrait, upper photo 50%, lower illustration panel 50%. Best match for a side-by-side-in-time comparison and the default reference look.
- `postcard`: 3:4 portrait, upper photo 70%, lower illustration panel 30%. Best when the photograph should dominate.

Both layouts preserve the source photo only through EXIF correction, crop or contain placement, and resize. If `cover` cuts a required feature, adjust focus coordinates or use `contain`; never reconstruct the missing area with image generation.
