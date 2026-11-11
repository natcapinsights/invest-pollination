# invest-pollination

NCI version of InVEST Crop Pollination model with added attribution and crop yield functions

This extended version of InVEST Crop Pollination model adds the following capabilities:

- Calculating total pollinator supply as the primary biophysical output raster, replacing InVEST's floral-weighted visitation measure
- Recalculating total pollinator visitation without floral-weighting
- Calculating pixel-level crop yields and value per hectare
- Attribute crop yield back to pollinator source locations on the landscape to link ecosystem service demand (crop yield) to supply (pollinator habitat)
- Computing nesting density by species for marginal value calculations
- Scenario analysis to calculate the marginal changes in pollinator supply and crop yield based on changes between two landcover maps

## New data requirements

There are two additional sub-modules with optionally-required input data, each toggled by a boolean argument (`crop_yield`, `scenario_analysis`).

### Crop Yield

The following columns are newly required in the biophysical table if the `crop_yield` modeling option is selected:

- `crop_pollinator_dependence_index`: the proportion of annual crop yield dependent on pollinators (0 to 1 index)
- `half_saturation_coefficient` (0 to 1): the half saturation coefficient for crop yield modeling (0 to 1 index)
- `crop_value_per_hectare`: the annual crop yield value per hectare (currency/ha/yr)

### Scenario Analysis

The following input data are required if the `scenario_analysis` modeling option is selected:

- `scenario_landcover_raster_path`: a path to a LULC raster depicting an alternative landscape scenario (e.g., habitat restoration, urban expansion, etc.)
- `scenario_suffix` (optional): an optional suffix to append after the `results_suffix`. If not provided, the model will use "scenario" as a default

## New output rasters

### Core pollination outputs

| Output | Description | Filename |
| --- | --- | --- |
| Pollinator supply | Total pollinator supply across all species. This is considered the primary biophysical output and can be considered an index of pollinator habitat quality | `pollinator_supply_index.tif` |
| Pollinator visitation | Total pollinator visitation across all species, replacing InVEST's floral-weighted visitation measure | `pollinator_visitation_index.tif` |
| Habitat nesting density | Nesting density by species, used in marginal value calculations | `intermediate_outputs/habitat_nesting_density_[SPECIES].tif` |

### Crop yield outputs (`crop_yield` option)

| Output | Description | Filename |
| --- | --- | --- |
| Annual crop yield value | Annual value of pollinator-dependent crop yield per hectare | `annual_crop_yield_value.tif` |
| Attributed annual crop yield value | Crop yield value attributed back to the pollinator source locations that supply it, linking demand (crop yield) to supply (pollinator habitat) | `attributed_annual_crop_yield_value.tif` |
| Attributed annual crop yield index | Attributed pollinator-dependent crop yield (summed 0 to 1 indices) | `intermediate_outputs/attributed_annual_crop_yield_index.tif` |
| Attributed crop yield by species | Per-species attribution of crop yield index/value to source locations | `intermediate_outputs/attributed_annual_crop_yield_index_[SPECIES].tif`, `intermediate_outputs/attributed_annual_crop_yield_value_[SPECIES].tif` |

### Scenario analysis outputs (`scenario_analysis` option)

| Output | Description | Filename |
| --- | --- | --- |
| Attributed change in pollinator supply | Changes in pollinator supply attributed back to the scenario locations that caused it, across all species | `d_pollinator_supply_index.tif` |
| Attributed change in annual crop yield value | Changes in annual crop yield value due to changes in pollinator supply, attributed back to the scenario locations that caused it | `d_annual_crop_yield_value.tif` |
| Attributed change in annual crop yield | Attributed changes in pollinator-dependent crop yield (summed 0 to 1 indices) | `intermediate_outputs/d_annual_crop_yield_index.tif` |
| Attributed change in pollinator supply by species | Attributed changes in pollinator supply for the given species | `intermediate_outputs/d_pollinator_supply_[SPECIES].tif` |

Most other intermediate calculation rasters (e.g. relative and convolved crop yield indices, per-species crop yield derivatives) are written to `intermediate_outputs/` and are not part of the primary output set.

## Methods

[leaving this to fill in from Eric's writeups of the pollinator MV and crop attribution models]