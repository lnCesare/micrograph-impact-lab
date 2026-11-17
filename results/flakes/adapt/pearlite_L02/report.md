# Flake measurements: pearlite_L02

1 images; reading: **boundaries** (ambiguous profile; boundary reading by default (the agent review checks it)); flake width 10.0 px; code version 2d70afd967.

## Confidence

- Twin accuracy (held-out twin images, exact truth): worst family ratio 0.72, pooled 0.42 (<= 1 means within the tolerances of config.yaml); dev 0.61: a gap shows how much the search fitted the dev twin images.
- The twin resembles the real images (worst profile mismatch x1.14; flake aspect ratio and width as measured by the method differ by up to x1.15); the twin accuracy carries over only as far as that, and only for structures the generator draws (straight laths in colonies).
- Rotation check on the real images (central disk turned by 30 deg and measured again): worst ratio 1.97 (FAILS); S changes by 0.0591, the direction by 2.267 deg, the median aspect ratio by 0.0799 (relative); for comparison, the 95 % interval of S from the finite number of flakes is +/-0.111.
- The settings tuned on the twin were **not used**: on the real images they gave a worse rotation check (flakes used 487 vs 489, rotation check 4.340833333333333 vs 1.97 with the start settings), so the start settings were kept: the twin did not capture what made the difference.
- **Two scales**: 53% of the bright phase is in structures about 45 px wide, 4x the typical flake width used here (10.0 px). The settings fit the fine structures; the coarse ones may be split, merged or left out, and the statistics mix the two populations. If the coarse ones are the flakes you mean, rerun with --flake-width 45.
- The image profile was ambiguous between a boundary network and phase islands: check in the drawings that the outlined regions are the flakes you mean; rerun with --mode phase otherwise.
- Lengths are in pixels (no --um-per-px given).

## Set summary

| | median | min | max |
|---|---|---|---|
| order parameter S | 0.449 | 0.449 | 0.449 |
| local S (within colonies) | 0.746 | 0.746 | 0.746 |
| mean misalignment (deg) | 29 | 29 | 29 |
| median aspect ratio | 2.41 | 2.41 | 2.41 |
| median length (px) | 33.1 | 33.1 | 33.1 |
| median width (px) | 11.5 | 11.5 | 11.5 |
| flakes used per image | 489 | 489 | 489 |

Mean directions differ per image (axial; see the table): a set-level mean direction is only meaningful when the images were taken in the same specimen frame.

## Per image

| image | mean dir (deg) | S [95 % CI] | local S | misalignment (deg) | AR [95 % CI] | flakes | scratches |
|---|---|---|---|---|---|---|---|
| L02__10.3390_ma18204798_g006_p4 | 146 | 0.449 [0.339, 0.561] | 0.746 | 29 | 2.41 [2.28, 2.6] | 489 | 0 |

## How accurate is it (twin images with exact truth)

Twin: 18 code-generated images fitted to this set (realism ok: True; worst profile mismatch x1.14).

| twin images | params | mean direction err (deg) | S err | misalignment err (deg) | AR err | flake orientation err (deg) | flakes found | worst family ratio |
|---|---|---|---|---|---|---|---|---|
| dev | start | 0.332 | 0.0177 | 0.827 | 0.0809 | 1.74 | 0.785 | 0.608 |
| holdout | start | 0.336 | 0.0208 | 1.18 | 0.058 | 1.73 | 0.785 | 0.721 |
| dev | chosen | 0.332 | 0.0177 | 0.827 | 0.0809 | 1.74 | 0.785 | 0.608 |
| holdout | chosen | 0.336 | 0.0208 | 1.18 | 0.058 | 1.73 | 0.785 | 0.721 |

Search: 20 new configurations evaluated (31 incl. cached); objective 0.743 at the start, 0.743 chosen.
Warm start: ti_basketweave_ma19010080 (distance 0.46), ti64_dev (distance 0.51).

## Real images: rotation check

Central disk measured as is and turned by 30 deg: direction off by 2.267 deg, S by 0.0591, aspect ratio by 0.0799 (worst ratio to tolerance 1.97; <= 1 passes). Necessary, not sufficient.

## Drawings (left: original, right: flakes coloured by orientation; grey = not used)

![L02__10](review/L02__10.3390_ma18204798_g006_p4.jpg)

![rose.png](rose.png)
![aspect_ratio.png](aspect_ratio.png)

## Files

- `flakes.csv`: every flake (orientation, length, width, aspect ratio, misalignment, flags)
- `image_summaries.csv`: one row per image, with bootstrap intervals
- `params.json`: the params used
- `adapt_log.json`: profile, twin, search trials, reviews
