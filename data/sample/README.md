# data/sample

A small subset of the data, for tests.
The full dataset stays outside the repository;
`paths.dataset` in [config.yaml](../../config.yaml), or the environment variable `MICROGRAPHS_DIR`, points at it.

Only files whose licence has been checked are here.
The synthetic set is not redistributed (provenance unconfirmed, see the Data section of [README.md](../../README.md)),
except one figure in [docs/](../../docs/) published with the scientist's agreement;
the tests build their own synthetic micrograph in code ([tests/voronoi.py](../../tests/voronoi.py)).

The two 316L files come from the dataset's `train` folder,
which neither the dev split (`val`) nor the holdout split (`test`) uses.

| File | Source | Licence |
|---|---|---|
| `316L/images/A_01_01.png`, `316L/masks/A_01_01.png` | 316L stainless steel, `Grain/train/`, Warren et al. 2024, [Zenodo 10.5281/zenodo.22039777](https://doi.org/10.5281/zenodo.22039777) | CC BY 4.0 |
| `316L/images/RG10_1_1.jpg`, `316L/masks/RG10_1_1.png` | same | CC BY 4.0 |

Masks: 1 = grain interior, 0 = grain boundary.

| File | Source | Licence |
|---|---|---|
| `Ti64/Ti64_21.jpg` | Lamellar Ti-6Al-4V, optical; White, Quinta da Fonseca et al., University of Manchester, *Lamellar and Bi-Modal Ti-64 Microstructure Images*, [Zenodo 5714384](https://zenodo.org/records/5714384) | CC BY 4.0 |

`Ti64_21.jpg` is in the Ti64 dev split, never the holdout; a test on it is a smoke test (the Ti64 images have no ground truth).
