# Geographic data and preview notices

The original project code is licensed under the [MIT License](LICENSE).
Geographic data and derived previews are not covered by that licence; the MIT
License does not replace the source terms below.

## Attribution for previews

The images in `previews/` are derived renderings from:

- Tokyo Metropolitan Government / Project PLATEAU, Shinjuku FY2025 city model.
- Expanded Shinjuku studies also use Shibuya and Nakano FY2025 city-model tiles
  for the same camera's surrounding buildings and shadow casters.
- MLIT Project PLATEAU, PLATEAU-Ortho 2023.
- Terrain: **PLATEAU | Mapterhorn | 国土地理院**.

This project selected, transformed, cropped and rendered the source data.
Building textures are retained; the geometry comparison replaces materials.
The original `shinjuku-isometric.png` and `shinjuku-geometry.png` previews have
no AI styling. Later art studies declare styling and inferred vegetation
separately in their manifests and captions.
This is an independent project, not an official MLIT, Tokyo Metropolitan
Government or Project PLATEAU product or endorsement.

## Buildings

The [Shinjuku FY2025 catalog](https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2025)
permits reproduction, public transmission, transformation and commercial use.
The original dataset README offers [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
as a selectable licence. Use the building data under that licence, preserve
source credit and identify modifications. The local download manifest records
the exact source URLs, checksums and selection bounds.

Expanded source packages also reference the
[Shibuya FY2025 catalog](https://www.geospatial.jp/ckan/dataset/plateau-13113-shibuya-ku-2025)
and [Nakano FY2025 catalog](https://www.geospatial.jp/ckan/dataset/plateau-13114-nakano-ku-2025).
Their recorded catalog licence points to the [PLATEAU site policy](https://www.mlit.go.jp/plateau/site-policy/),
which permits CC BY 4.0 use for content covered by its public-data terms, subject
to specific notices. Preserve the supplying municipality, package identity and
modification notice; these surrounding tiles are not relabeled as Shinjuku data.

## Orthophotography and terrain

[PLATEAU-Ortho documentation](https://docs.plateauview.mlit.go.jp/datasets/ortho/)
describes reuse under CC BY 4.0-compatible terms. The source can combine PLATEAU
and Geospatial Information Authority of Japan imagery.

[PLATEAU terrain documentation](https://docs.plateauview.mlit.go.jp/datasets/terrain/)
specifies the credit **PLATEAU | Mapterhorn | 国土地理院**. Preserve that credit
when displaying or redistributing the terrain or derived renderings.

Consult the [PLATEAU policy](https://www.mlit.go.jp/plateau/site-policy/)
and dataset-specific notices for attribution, modified-content notices and any
third-party conditions. CC licensing does not grant unrelated trademark,
privacy or other rights. Surveying uses may have separate Survey Act requirements.

The FY2025 building edition and 2023 orthophoto service label are not guarantees
of a uniform capture date. See [the study notes](README.txt) for source history.

Raw downloads, packed Blender projects and local source-kit archives are excluded
from this repository. This exclusion reduces repository size; it does not remove
attribution obligations from the previews or make source data private.

## Mapped vegetation and land cover

Vegetation supplements derived from OpenStreetMap retain **© OpenStreetMap
contributors** attribution. The source data and derived vegetation databases are
available under the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/),
not the code's MIT licence. See [OpenStreetMap copyright](https://www.openstreetmap.org/copyright)
and [attribution guidance](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines).

Mapped tree points and wooded/grass polygons are distinct from surveyed PLATEAU
geometry. Generated canopy shapes and placements inside mapped woodland are
artistic representations, not measured individual trees. Preserve this
qualification, data attribution and licence information with redistributed
vegetation data and renders that use it. Rendered maps display the relevant
source credit beside the image or in the visible map attribution.
