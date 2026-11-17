# Flake measurements: needles_L23

1 images; reading: **phase** (minority phase is separate blobs about as thick as the matrix); flake width 4.0 px; code version bda0a7c33b.

## Confidence

- **Twin not usable** for this image set (it does not resemble the images closely enough, or flakes cannot be found on it at this size): the accuracy below is not evidence about these images, and no search was run on it; the scaled default settings were used. Treat the numbers as unvalidated; check them in the drawings.
- Twin accuracy (held-out twin images, exact truth): worst family ratio 3.52, pooled 3.17 (<= 1 means within the tolerances of config.yaml); dev 3.52: a gap shows how much the search fitted the dev twin images.
- The twin resembles the real images only roughly (worst profile mismatch x1.03; flake aspect ratio and width as measured by the method differ by up to x1.91); the twin accuracy carries over only as far as that, and only for structures the generator draws (straight laths in colonies).
- Rotation check on the real images (central disk turned by 30 deg and measured again): worst ratio 0.7 (passes); S changes by 0.0211, the direction by None deg, the median aspect ratio by 0.0056 (relative); for comparison, the 95 % interval of S from the finite number of flakes is +/-0.026. At S below ~0.3 the mean direction is weakly defined and moves easily.
- Lengths are in pixels (no --um-per-px given).

## Set summary

| | median | min | max |
|---|---|---|---|
| order parameter S | 0.035 | 0.035 | 0.035 |
| local S (within colonies) | 0.335 | 0.335 | 0.335 |
| mean misalignment (deg) | 43.8 | 43.8 | 43.8 |
| median aspect ratio | 2.06 | 2.06 | 2.06 |
| median length (px) | 13.2 | 13.2 | 13.2 |
| median width (px) | 5.84 | 5.84 | 5.84 |
| flakes used per image | 1e+04 | 1e+04 | 1e+04 |

Mean directions differ per image (axial; see the table): a set-level mean direction is only meaningful when the images were taken in the same specimen frame.

## Per image

| image | mean dir (deg) | S [95 % CI] | local S | misalignment (deg) | AR [95 % CI] | flakes | scratches |
|---|---|---|---|---|---|---|---|
| L23__rank003__066_Primaerzementit__CC-BY-SA-3.0 | 48.3 | 0.035 [0.0121, 0.0644] | 0.335 | 43.8 | 2.06 [2.04, 2.08] | 10015 | 0 |

## How accurate is it (twin images with exact truth)

Twin: 18 code-generated images fitted to this set (realism ok: False; worst profile mismatch x1.03).

| twin images | params | mean direction err (deg) | S err | misalignment err (deg) | AR err | flake orientation err (deg) | flakes found | worst family ratio |
|---|---|---|---|---|---|---|---|---|
| dev | start | 0.31 | 0.00807 | 0.466 | 0.489 | 1.33 | 0.93 | 3.52 |
| holdout | start | 0.64 | 0.00889 | 0.674 | 0.475 | 1.35 | 0.936 | 3.52 |
| dev | chosen | 0.31 | 0.00807 | 0.466 | 0.489 | 1.33 | 0.93 | 3.52 |
| holdout | chosen | 0.64 | 0.00889 | 0.674 | 0.475 | 1.35 | 0.936 | 3.52 |

Search: 2 new configurations evaluated (7 incl. cached); objective 4.333 at the start, 4.333 chosen.
Warm start: ti64_dev (distance 6.12).

## Real images: rotation check

Central disk measured as is and turned by 30 deg: direction off by None deg, S by 0.0211, aspect ratio by 0.0056 (worst ratio to tolerance 0.7033333333333334; <= 1 passes). Necessary, not sufficient.

## Drawings (left: original, right: flakes coloured by orientation; grey = not used)

![L23__rank003__066_Primaerzementit__CC-BY-SA-3](overlays/L23__rank003__066_Primaerzementit__CC-BY-SA-3.jpg)

![rose.png](rose.png)
![aspect_ratio.png](aspect_ratio.png)

## Files

- `flakes.csv`: every flake (orientation, length, width, aspect ratio, misalignment, flags)
- `image_summaries.csv`: one row per image, with bootstrap intervals
- `params.json`: the params used
- `adapt_log.json`: profile, twin, search trials, reviews
