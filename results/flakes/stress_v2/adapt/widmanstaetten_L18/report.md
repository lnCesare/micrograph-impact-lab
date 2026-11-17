# Flake measurements: widmanstaetten_L18

1 images; reading: **boundaries** (ambiguous profile; boundary reading by default (the agent review checks it)); flake width 13.42 px; code version bda0a7c33b.

## Confidence

- **Twin not usable** for this image set (it does not resemble the images closely enough, or flakes cannot be found on it at this size): the accuracy below is not evidence about these images, and no search was run on it; the scaled default settings were used. Treat the numbers as unvalidated; check them in the drawings.
- Twin accuracy (held-out twin images, exact truth): worst family ratio 0.92, pooled 0.56 (<= 1 means within the tolerances of config.yaml); dev 0.97: a gap shows how much the search fitted the dev twin images.
- The twin resembles the real images only roughly (worst profile mismatch x2.21; flake aspect ratio and width as measured by the method differ by up to x1.78); the twin accuracy carries over only as far as that, and only for structures the generator draws (straight laths in colonies).
- Rotation check on the real images (central disk turned by 30 deg and measured again): worst ratio 0.35 (passes); S changes by 0.0104, the direction by 1.0412 deg, the median aspect ratio by 0.0163 (relative); for comparison, the 95 % interval of S from the finite number of flakes is +/-0.193.
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

Twin: 18 code-generated images fitted to this set (realism ok: False; worst profile mismatch x2.21).

| twin images | params | mean direction err (deg) | S err | misalignment err (deg) | AR err | flake orientation err (deg) | flakes found | worst family ratio |
|---|---|---|---|---|---|---|---|---|
| dev | start | 0.121 | 0.0169 | 1.02 | 0.0788 | 1.89 | 0.655 | 0.681 |
| holdout | start | 0.241 | 0.0193 | 0.998 | 0.0749 | 1.83 | 0.672 | 0.909 |
| dev | chosen | 0.357 | 0.0342 | 1.49 | 0.0299 | 2.16 | 0.961 | 0.973 |
| holdout | chosen | 0.128 | 0.0281 | 1.46 | 0.0523 | 2.03 | 0.966 | 0.923 |

Search: 2 new configurations evaluated (7 incl. cached); objective 0.813 at the start, 1.144 chosen.
Warm start: ti64_dev (distance 0.82).

## Real images: rotation check

Central disk measured as is and turned by 30 deg: direction off by 1.0412 deg, S by 0.0104, aspect ratio by 0.0163 (worst ratio to tolerance 0.34706666666666663; <= 1 passes). Necessary, not sufficient.

## Drawings (left: original, right: flakes coloured by orientation; grey = not used)

![L18__rank078__043_Widmannstaetten__CC-BY-SA-3](overlays/L18__rank078__043_Widmannstaetten__CC-BY-SA-3.jpg)

![rose.png](rose.png)
![aspect_ratio.png](aspect_ratio.png)

## Files

- `flakes.csv`: every flake (orientation, length, width, aspect ratio, misalignment, flags)
- `image_summaries.csv`: one row per image, with bootstrap intervals
- `params.json`: the params used
- `adapt_log.json`: profile, twin, search trials, reviews
