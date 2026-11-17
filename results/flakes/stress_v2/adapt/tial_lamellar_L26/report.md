# Flake measurements: tial_lamellar_L26

1 images; reading: **boundaries** (ambiguous profile; boundary reading by default (the agent review checks it)); flake width 18.44 px; code version bda0a7c33b.

## Confidence

- **Twin not usable** for this image set (it does not resemble the images closely enough, or flakes cannot be found on it at this size): the accuracy below is not evidence about these images, and no search was run on it; the scaled default settings were used. Treat the numbers as unvalidated; check them in the drawings.
- Twin accuracy (held-out twin images, exact truth): worst family ratio 2.14, pooled 1.76 (<= 1 means within the tolerances of config.yaml); dev 2.11: a gap shows how much the search fitted the dev twin images.
- The twin resembles the real images only roughly (worst profile mismatch x1.49; flake aspect ratio and width as measured by the method differ by up to x1.65); the twin accuracy carries over only as far as that, and only for structures the generator draws (straight laths in colonies).
- Rotation check on the real images (central disk turned by 30 deg and measured again): worst ratio 0.97 (passes); S changes by 0.0219, the direction by 0.0477 deg, the median aspect ratio by 0.0487 (relative); for comparison, the 95 % interval of S from the finite number of flakes is +/-0.116.
- The image profile was ambiguous between a boundary network and phase islands: check in the drawings that the outlined regions are the flakes you mean; rerun with --mode phase otherwise.
- Lengths are in pixels (no --um-per-px given).

## Set summary

| | median | min | max |
|---|---|---|---|
| order parameter S | 0.56 | 0.56 | 0.56 |
| local S (within colonies) | 0.895 | 0.895 | 0.895 |
| mean misalignment (deg) | 23.8 | 23.8 | 23.8 |
| median aspect ratio | 2.96 | 2.96 | 2.96 |
| median length (px) | 95.1 | 95.1 | 95.1 |
| median width (px) | 29.2 | 29.2 | 29.2 |
| flakes used per image | 989 | 989 | 989 |

Mean directions differ per image (axial; see the table): a set-level mean direction is only meaningful when the images were taken in the same specimen frame.

## Per image

| image | mean dir (deg) | S [95 % CI] | local S | misalignment (deg) | AR [95 % CI] | flakes | scratches |
|---|---|---|---|---|---|---|---|
| L26__rank038__056_TiAl_Lammelar__CC0 | 117 | 0.56 [0.437, 0.67] | 0.895 | 23.8 | 2.96 [2.8, 3.17] | 989 | 0 |

## How accurate is it (twin images with exact truth)

Twin: 18 code-generated images fitted to this set (realism ok: False; worst profile mismatch x1.49).

| twin images | params | mean direction err (deg) | S err | misalignment err (deg) | AR err | flake orientation err (deg) | flakes found | worst family ratio |
|---|---|---|---|---|---|---|---|---|
| dev | start | 0.487 | 0.0154 | 0.739 | 0.0283 | 2.12 | 0.774 | 0.53 |
| holdout | start | 0.596 | 0.0337 | 1.66 | 0.0296 | 2.17 | 0.509 | 2.59 |
| dev | chosen | 0.55 | 0.0588 | 2.47 | 0.287 | 2.67 | 0.896 | 2.11 |
| holdout | chosen | 0.616 | 0.0881 | 3.84 | 0.202 | 2.78 | 0.444 | 2.14 |

Search: 2 new configurations evaluated (7 incl. cached); objective 0.636 at the start, 2.591 chosen.
Warm start: ti64_dev (distance 0.93).

## Real images: rotation check

Central disk measured as is and turned by 30 deg: direction off by 0.0477 deg, S by 0.0219, aspect ratio by 0.0487 (worst ratio to tolerance 0.974; <= 1 passes). Necessary, not sufficient.

## Drawings (left: original, right: flakes coloured by orientation; grey = not used)

![L26__rank038__056_TiAl_Lammelar__CC0](review/L26__rank038__056_TiAl_Lammelar__CC0.jpg)

![rose.png](rose.png)
![aspect_ratio.png](aspect_ratio.png)

## Files

- `flakes.csv`: every flake (orientation, length, width, aspect ratio, misalignment, flags)
- `image_summaries.csv`: one row per image, with bootstrap intervals
- `params.json`: the params used
- `adapt_log.json`: profile, twin, search trials, reviews
