# Flake measurements: widmanstaetten_L18

1 images; reading: **boundaries** (ambiguous profile; boundary reading by default (the agent review checks it)); flake width 13.42 px; code version 2d70afd967.

## Confidence

- **Twin not usable** for this image set (it does not resemble the images closely enough, or flakes cannot be found on it at this size): the accuracy below is not evidence about these images, and no search was run on it; the scaled default settings were used. Treat the numbers as unvalidated; check them in the drawings.
- Twin accuracy (held-out twin images, exact truth): worst family ratio 2.05, pooled 1.23 (<= 1 means within the tolerances of config.yaml); dev 2.05: a gap shows how much the search fitted the dev twin images.
- The twin resembles the real images only roughly (worst profile mismatch x1.73; flake aspect ratio and width as measured by the method differ by up to x1.35); the twin accuracy carries over only as far as that, and only for structures the generator draws (straight laths in colonies).
- Rotation check on the real images (central disk turned by 30 deg and measured again): worst ratio 0.35 (passes); S changes by 0.0104, the direction by 1.0412 deg, the median aspect ratio by 0.0163 (relative); for comparison, the 95 % interval of S from the finite number of flakes is +/-0.193.
- **Two scales**: 57% of the bright phase is in structures about 62 px wide, 5x the typical flake width used here (13.4 px). The settings fit the fine structures; the coarse ones may be split, merged or left out, and the statistics mix the two populations. If the coarse ones are the flakes you mean, rerun with --flake-width 62.
- The image profile was ambiguous between a boundary network and phase islands: check in the drawings that the outlined regions are the flakes you mean; rerun with --mode phase otherwise.
- Lengths are in pixels (no --um-per-px given).

## Set summary

| | median | min | max |
|---|---|---|---|
| order parameter S | 0.533 | 0.533 | 0.533 |
| local S (within colonies) | 0.742 | 0.742 | 0.742 |
| mean misalignment (deg) | 23.6 | 23.6 | 23.6 |
| median aspect ratio | 1.99 | 1.99 | 1.99 |
| median length (px) | 27.4 | 27.4 | 27.4 |
| median width (px) | 12 | 12 | 12 |
| flakes used per image | 397 | 397 | 397 |

Mean directions differ per image (axial; see the table): a set-level mean direction is only meaningful when the images were taken in the same specimen frame.

## Per image

| image | mean dir (deg) | S [95 % CI] | local S | misalignment (deg) | AR [95 % CI] | flakes | scratches |
|---|---|---|---|---|---|---|---|
| L18__rank078__043_Widmannstaetten__CC-BY-SA-3.0 | 179 | 0.533 [0.313, 0.698] | 0.742 | 23.6 | 1.99 [1.87, 2.08] | 397 | 0 |

## How accurate is it (twin images with exact truth)

Twin: 18 code-generated images fitted to this set (realism ok: False; worst profile mismatch x1.73).

| twin images | params | mean direction err (deg) | S err | misalignment err (deg) | AR err | flake orientation err (deg) | flakes found | worst family ratio |
|---|---|---|---|---|---|---|---|---|
| dev | start | 0.177 | 0.0197 | 1.04 | 0.0755 | 2.13 | 0.723 | 0.598 |
| holdout | start | 0.337 | 0.0171 | 1.03 | 0.0734 | 2.18 | 0.736 | 0.731 |
| dev | chosen | 0.203 | 0.071 | 3.41 | 0.0234 | 2.85 | 0.924 | 2.05 |
| holdout | chosen | 0.566 | 0.0613 | 2.87 | 0.0232 | 2.8 | 0.924 | 2.05 |

Search: 3 new configurations evaluated (8 incl. cached); objective 0.724 at the start, 2.404 chosen.
Warm start: ti_basketweave_ma19010080 (distance 0.38), pearlite_L02 (distance 0.49).

## Real images: rotation check

Central disk measured as is and turned by 30 deg: direction off by 1.0412 deg, S by 0.0104, aspect ratio by 0.0163 (worst ratio to tolerance 0.34706666666666663; <= 1 passes). Necessary, not sufficient.

## Drawings (left: original, right: flakes coloured by orientation; grey = not used)

![L18__rank078__043_Widmannstaetten__CC-BY-SA-3](review/L18__rank078__043_Widmannstaetten__CC-BY-SA-3.0.jpg)

![rose.png](rose.png)
![aspect_ratio.png](aspect_ratio.png)

## Files

- `flakes.csv`: every flake (orientation, length, width, aspect ratio, misalignment, flags)
- `image_summaries.csv`: one row per image, with bootstrap intervals
- `params.json`: the params used
- `adapt_log.json`: profile, twin, search trials, reviews
