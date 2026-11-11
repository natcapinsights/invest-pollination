from natcap.invest import gettext
from natcap.invest import spec
from natcap.invest.spec import u
from natcap.invest.pollination import MODEL_SPEC as input_model_spec

# Define component parts of the model spec prior to creation, so as to use the update= argument correctly

# 1. Define basic model input parameters

model_id = "nci_pollination"
model_title = "NCI Pollination"


# 2. Create inputs and input field order

# Remove farm-related fields and files from model spec
_farm_input_ids = ["farm_vector_path"]
input_field_order = [
    [x for x in sublist if x not in _farm_input_ids]
    for sublist in input_model_spec.input_field_order
]
inputs = [x for x in input_model_spec.inputs if x.id not in _farm_input_ids]

# Get index of the landcover table
for i, input_spec in enumerate(inputs):
    if input_spec.id == "landcover_biophysical_table_path":
        _landcover_table_index = i
        break

# Replace the landcover table spec with the new one that has the additional columns
# for pixel-level crop yield modeling
inputs[_landcover_table_index] = spec.CSVInput(
    id="landcover_biophysical_table_path",
    name=gettext("biophysical table"),
    about=gettext(
        "A table mapping each LULC class to nesting availability and floral"
        " abundance data for each substrate and season in that LULC class,"
        " as well as crop pollination dependency and value where appropriate."
        " All values in the LULC raster must have corresponding entries in this"
        " table."
    ),
    columns=[
        spec.LULC_TABLE_COLUMN,
        spec.RatioInput(
            id="nesting_[SUBSTRATE]_availability_index",
            about=gettext(
                "Index of availability of the given substrate in this LULC class."
                " Replace [SUBSTRATE] with substrate names matching those in the"
                " Guild Table, so that there is a column for each substrate."
            ),
            units=None,
        ),
        spec.RatioInput(
            id="floral_resources_[SEASON]_index",
            about=gettext(
                "Abundance of flowers during the given season in this LULC class."
                " This is the proportion of land area covered by flowers,"
                " multiplied by the proportion of the season for which there is"
                " that coverage. Replace [SEASON] with season names matching"
                " those in the Guild Table, so that there is a column for each"
                " season."
            ),
            units=None,
        ),
        spec.RatioInput(
            id="crop_pollinator_dependence_index",
            about=gettext("Proportion of annual crop yield dependent on pollinators."),
            required="crop_yield",
            units=None,
        ),
        spec.RatioInput(
            id="half_saturation_coefficient",
            about=gettext(
                "Half saturation coefficient for crop yield modeling."  # TODO add more explanation of what this means
            ),
            required="crop_yield",
            units=None,
        ),
        spec.NumberInput(
            id="crop_value_per_hectare",
            about=gettext("Annual crop yield value per hectare."),
            required="crop_yield",
            units=u.currency / u.hectare / u.year,
        ),
    ],
    index_col="lucode",
)

# Extend crop yield analysis
input_field_order.extend(
    [
        ["crop_yield"],
        ["scenario_analysis", "scenario_landcover_raster_path", "scenario_suffix"],
    ]
)
inputs.extend(
    [
        spec.BooleanInput(
            id="crop_yield",
            name=gettext("calculate pollination-dependent crop yield"),
            about=gettext("Calculate pollination-dependent crop yield and value."),
        ),
        spec.BooleanInput(
            id="scenario_analysis",
            name=gettext("calculate marginal value from scenario"),
            about=gettext(
                "Calculate marginal value for pollination from a scenario raster."
            ),
        ),
        spec.SingleBandRasterInput(
            id="scenario_landcover_raster_path",
            name=gettext("scenario land use/land cover"),
            about=gettext(
                "Map of LULC codes for the scenario. All values in this raster must have corresponding"
                " entries in the Biophysical Table."
            ),
            data_type=int,
            units=None,
            projected=True,
            required="scenario_analysis",
            allowed="scenario_analysis",
        ),
        spec.ResultsSuffixInput(
            id="scenario_suffix",
            name=gettext("scenario file suffix"),
            about=gettext(
                "Suffix that will be appended to all output file names for the scenario being evaluated."
            ),
            required=False,
            allowed="scenario_analysis",
            regexp="[a-zA-Z0-9_-]*",
        ),
    ]
)


# 3. Create outputs
_farm_output_ids = [
    "farm_results",
    "farm_pollinators",
    "total_pollinator_yield",
    "wild_pollinator_yield",
    "blank_raster",
    "farm_nesting_substrate_index_[SUBSTRATE]",
    "farm_pollinator_[SEASON]",
    "farm_relative_floral_abundance_index_[SEASON]",
    "half_saturation_[SEASON]",
    "managed_pollinators",
    "reprojected_farm_vector",
]
outputs = [x for x in input_model_spec.outputs if x.id not in _farm_output_ids]

# Assign archivable outputs to archival folder
for output_spec_id in [
    "pollinator_abundance_[SPECIES]_[SEASON]",
    "total_pollinator_abundance_[SEASON]",
]:
    for i, input_spec in enumerate(outputs):
        if input_spec.id == output_spec_id:
            index = i
            about = input_spec.about
            data_type = input_spec.data_type
            units = input_spec.units
            break
    outputs[index] = spec.SingleBandRasterOutput(
        id=output_spec_id,
        path=f"archived_outputs/{output_spec_id}.tif",
        about=about,
        data_type=data_type,
        units=units,
    )


outputs.extend(
    [
        # New outputs for pollinator supply and crop yield modeling
        spec.SingleBandRasterOutput(
            id="pollinator_supply_index",
            path="pollinator_supply_index.tif",
            about=gettext("Total pollinator supply across all species"),
            data_type=float,
            units=None,
        ),
        spec.SingleBandRasterOutput(
            id="habitat_nesting_density_[SPECIES]",
            path="intermediate_outputs/habitat_nesting_density_[SPECIES].tif",
            about=gettext("Habitat nesting density for the given species"),
            data_type=float,
            units=None,
        ),
        spec.SingleBandRasterOutput(
            id="pollinator_visitation_index",
            path="pollinator_visitation_index.tif",
            about=gettext("Total pollinator visitation across all species"),
            data_type=float,
            units=None,
        ),
        # Crop yield-related outputs
        spec.SingleBandRasterOutput(
            id="crop_pollinator_dependence_index",
            path="intermediate_outputs/crop_pollinator_dependence_index.tif",
            about=gettext("Crop pollinator dependence for the given crop"),
            data_type=float,
            units=None,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="half_saturation_coefficient",
            path="intermediate_outputs/half_saturation_coefficient.tif",
            about=gettext("Half saturation coefficient for the given crop"),
            data_type=float,
            units=None,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="crop_value_per_hectare",
            path="intermediate_outputs/crop_value_per_hectare.tif",
            about=gettext("Crop value per hectare for the given crop"),
            data_type=float,
            units=u.currency / u.hectare / u.year,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="annual_crop_yield_index",
            path="intermediate_outputs/annual_crop_yield_index.tif",
            about=gettext("Annual crop yield index for the given crop"),
            data_type=float,
            units=None,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="annual_crop_yield_value",
            path="annual_crop_yield_value.tif",
            about=gettext("Annual value of crop yield for the given crop"),
            data_type=float,
            units=u.currency / u.hectare / u.year,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="relative_annual_crop_yield_index",
            path="intermediate_outputs/relative_annual_crop_yield_index.tif",
            about=gettext("Relative annual crop yield index for the given crop"),
            data_type=float,
            units=None,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="relative_annual_crop_yield_value",
            path="intermediate_outputs/relative_annual_crop_yield_value.tif",
            about=gettext("Relative annual value of crop yield for the given crop"),
            data_type=float,
            units=u.currency / u.hectare / u.year,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="convolved_relative_annual_crop_yield_index_[SPECIES]",
            path="intermediate_outputs/convolved_relative_annual_crop_yield_index_[SPECIES].tif",
            about=gettext(
                "Convolved relative annual crop yield index for the given crop and species"
            ),
            data_type=float,
            units=None,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="convolved_relative_annual_crop_yield_value_[SPECIES]",
            path="intermediate_outputs/convolved_relative_annual_crop_yield_value_[SPECIES].tif",
            about=gettext(
                "Convolved relative annual value of crop yield for the given crop and species"
            ),
            data_type=float,
            units=u.currency / u.hectare / u.year,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="attributed_annual_crop_yield_index_[SPECIES]",
            path="intermediate_outputs/attributed_annual_crop_yield_index_[SPECIES].tif",
            about=gettext(
                "Attributed annual crop yield index for the given crop and species"
            ),
            data_type=float,
            units=None,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="attributed_annual_crop_yield_value_[SPECIES]",
            path="intermediate_outputs/attributed_annual_crop_yield_value_[SPECIES].tif",
            about=gettext(
                "Attributed annual value of crop yield for the given crop and species"
            ),
            data_type=float,
            units=u.currency / u.year,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="attributed_annual_crop_yield_index",
            path="intermediate_outputs/attributed_annual_crop_yield_index.tif",
            about=gettext("Attributed annual crop yield index for the given crop"),
            data_type=float,
            units=None,
            created_if="crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="attributed_annual_crop_yield_value",
            path="attributed_annual_crop_yield_value.tif",
            about=gettext("Attributed annual value of crop yield for the given crop"),
            data_type=float,
            units=u.currency / u.year,
            created_if="crop_yield",
        ),
        # Pollinator supply-related outputs
        spec.SingleBandRasterOutput(
            id="d_pollinator_supply_[SPECIES]",
            path="intermediate_outputs/d_pollinator_supply_[SPECIES].tif",
            about=gettext("Change in pollinator supply for the given species"),
            data_type=float,
            units=None,
            created_if="scenario_analysis",
        ),
        spec.SingleBandRasterOutput(
            id="d_pollinator_supply_index",
            path="d_pollinator_supply_index.tif",
            about=gettext("Total change in pollinator supply across all species"),
            data_type=float,
            units=None,
            created_if="scenario_analysis",
        ),
        # Crop yield-related outputs
        spec.SingleBandRasterOutput(
            id="d_annual_crop_yield_index_d_pollinator_visitation",
            path="intermediate_outputs/d_annual_crop_yield_index_d_pollinator_visitation.tif",
            about=gettext(
                "Change in annual crop yield by change in pollinator visitation (dYield/dPV)"
            ),
            data_type=float,
            units=None,
            created_if="scenario_analysis and crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="d_annual_crop_yield_value_d_pollinator_visitation",
            path="intermediate_outputs/d_annual_crop_yield_value_d_pollinator_visitation.tif",
            about=gettext(
                "Change in annual crop yield value by change in pollinator visitation (dYieldValue/dPV)"
            ),
            data_type=float,
            units=None,  # TODO Check the units here
            created_if="scenario_analysis and crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="d_annual_crop_yield_index_d_pollinator_supply_[SPECIES]",
            path="intermediate_outputs/d_annual_crop_yield_index_d_pollinator_supply_[SPECIES].tif",
            about=gettext(
                "Functional relationship between change in annual crop yield by change in pollinator supply for the given species (dYield/dPS_species)"
            ),
            data_type=float,
            units=None,  # TODO Check the units here
            created_if="scenario_analysis and crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="d_annual_crop_yield_value_d_pollinator_supply_[SPECIES]",
            path="intermediate_outputs/d_annual_crop_yield_value_d_pollinator_supply_[SPECIES].tif",
            about=gettext(
                "Functional relationship between change in annual crop yield value by change in pollinator supply for the given species (dYieldValue/dPS_species)"
            ),
            data_type=float,
            units=None,  # TODO Check the units here
            created_if="scenario_analysis and crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="d_annual_crop_yield_index_[SPECIES]",
            path="intermediate_outputs/d_annual_crop_yield_index_[SPECIES].tif",
            about=gettext(
                "Change in annual crop yield due to changes in pollinator supply for the given species"
            ),
            data_type=float,
            units=None,  # TODO Check the units here
            created_if="scenario_analysis and crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="d_annual_crop_yield_value_[SPECIES]",
            path="intermediate_outputs/d_annual_crop_yield_value_[SPECIES].tif",
            about=gettext(
                "Change in annual crop yield value due to changes in pollinator supply for the given species"
            ),
            data_type=float,
            units=u.currency / u.hectare / u.year,
            created_if="scenario_analysis and crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="d_annual_crop_yield_index",
            path="intermediate_outputs/d_annual_crop_yield_index.tif",
            about=gettext(
                "Change in annual crop yield due to changes in pollinator supply"
            ),
            data_type=float,
            units=None,
            created_if="scenario_analysis and crop_yield",
        ),
        spec.SingleBandRasterOutput(
            id="d_annual_crop_yield_value",
            path="d_annual_crop_yield_value.tif",
            about=gettext(
                "Change in annual crop yield value due to changes in pollinator supply"
            ),
            data_type=float,
            units=u.currency / u.hectare / u.year,
            created_if="scenario_analysis and crop_yield",
        ),
    ]
)


# 4. Create model spec from the base
MODEL_SPEC = input_model_spec.model_copy(
    deep=True,
    update={
        "model_id": model_id,
        "model_title": model_title,
        "input_field_order": input_field_order,
        "inputs": inputs,
        "outputs": outputs,
    },
)
