# Standard library
import logging
import collections
import shutil
from pathlib import Path

# Third-party libraries
import numpy as np
from osgeo import gdal
import natcap.invest.utils
import natcap.invest.pollination
import pygeoprocessing
from natcap.invest import validation

# NCI
from .model_spec import MODEL_SPEC

# Logging
LOGGER = logging.getLogger(__name__)

_INDEX_NODATA = natcap.invest.pollination.pollination._INDEX_NODATA


# These patterns are expected in the biophysical table if calculating yield
_CROP_POLLINATOR_DEPENDENCE_FIELD = "crop_pollinator_dependence_index"
_HALF_SATURATION_FIELD = "half_saturation_coefficient"
_CROP_VALUE_FIELD = "crop_value_per_hectare"
_EXPECTED_BIOPHYSICAL_YIELD_HEADERS = [
    _CROP_POLLINATOR_DEPENDENCE_FIELD,
    _HALF_SATURATION_FIELD,
    _CROP_VALUE_FIELD,
]


def execute(
    args: dict,
):
    """Pollination model for NCI.

    Args:
        args (dict): dictionary of model arguments, as per InVEST requirements. See model spec for details.
    """

    # Creating string path args for InVEST model execution
    args_archive = args.copy()
    for arg, val in args_archive.items():
        if isinstance(val, Path):
            args[arg] = str(val)

    LOGGER.info("Starting pollination model")
    natcap.invest.pollination.execute(args)

    # Set up args, file registry, and scenario variables for NCI-specific processing
    args, file_registry, task_graph = MODEL_SPEC.setup(args)
    scenario_variables = _parse_scenario_variables(args)
    scenario_variables["pixel_area_ha"] = (
        scenario_variables["landcover_mean_pixel_size"] ** 2 / 10000
    )  # NOTE This assumes the landcover raster is in a projected coordinate system with linear units in meters

    # Move archival outputs from original InVEST model to archival folder
    _, invest_file_registry, _ = natcap.invest.pollination.MODEL_SPEC.setup(args)
    for season in scenario_variables["season_list"]:
        LOGGER.debug(
            f"Moving pollinator abundance outputs for {season} to archival folder"
        )
        shutil.move(
            invest_file_registry["total_pollinator_abundance_[SEASON]", season],
            file_registry["total_pollinator_abundance_[SEASON]", season],
        )

        for species in scenario_variables["species_list"]:
            LOGGER.debug(
                f"Moving pollinator abundance outputs for {species} in {season} to archival folder"
            )
            shutil.move(
                invest_file_registry[
                    "pollinator_abundance_[SPECIES]_[SEASON]", species, season
                ],
                file_registry[
                    "pollinator_abundance_[SPECIES]_[SEASON]", species, season
                ],
            )

    # Calculate total pollinator supply (or landscape score, in Eric's terms)
    LOGGER.debug(
        "Calculating total pollinator supply (landscape score) by summing across species"
    )
    pygeoprocessing.raster_calculator(
        [
            (file_registry["pollinator_supply_[SPECIES]", species], 1)
            for species in scenario_variables["species_list"]
        ],
        natcap.invest.pollination._sum_arrays,
        file_registry["pollinator_supply_index"],
        gdal.GDT_Float32,
        _INDEX_NODATA,
    )

    # Run convolutions to get nesting density per species
    for species in scenario_variables["species_list"]:
        LOGGER.debug(f"Calculating habitat nesting density for {species}")
        alpha = (
            scenario_variables["alpha_value"][species]
            / scenario_variables["landcover_mean_pixel_size"]
        )
        pygeoprocessing.convolve_2d(
            (
                file_registry["habitat_nesting_index_[SPECIES]", species],
                1,
            ),
            (file_registry["kernel_[ALPHA]", f"{alpha:.6f}"], 1),
            file_registry["habitat_nesting_density_[SPECIES]", species],
            ignore_nodata_and_edges=True,
            mask_nodata=True,
            normalize_kernel=False,
        )

    # Reclassify landcover to crop pollinator dependence,
    # half saturation coefficient, and crop value rasters
    reclass_error_details = {
        "raster_name": "LULC",
        "column_name": "lucode",
        "table_name": "Biophysical",
    }
    for field in _EXPECTED_BIOPHYSICAL_YIELD_HEADERS:
        LOGGER.debug(f"Reclassifying landcover to {field}")
        natcap.invest.utils.reclassify_raster(
            (args["landcover_raster_path"], 1),
            scenario_variables[field],
            file_registry[field],
            gdal.GDT_Float32,
            _INDEX_NODATA,
            reclass_error_details,
        )

    # Calculate expected pollinator visitation (not weighted by floral resources, which is the InVEST method)
    # by summing the convolved landscape scores (pollinator_supply in InVEST: nesting * forage * species relative abundance)
    # for each species
    LOGGER.debug(
        "Calculating expected pollinator visitation by summing convolved pollinator supplies (landscape scores) across species"
    )
    pygeoprocessing.raster_calculator(
        [
            (file_registry["convolve_ps_[SPECIES]", species], 1)
            for species in scenario_variables["species_list"]
        ],
        natcap.invest.pollination._sum_arrays,
        file_registry["pollinator_visitation_index"],
        gdal.GDT_Float32,
        _INDEX_NODATA,
    )

    # Perform crop yield analysis, if requested.
    if "crop_yield" in args and args["crop_yield"]:

        # Calculate pixel-wise yield using the yield_op function
        LOGGER.debug("Calculating pixel-wise yield using the yield_op function")
        pygeoprocessing.raster_map(
            op=yield_op,
            rasters=[
                file_registry[_CROP_POLLINATOR_DEPENDENCE_FIELD],
                file_registry[_HALF_SATURATION_FIELD],
                file_registry["pollinator_visitation_index"],
            ],
            target_path=file_registry["annual_crop_yield_index"],
            target_dtype=np.float32,
            target_nodata=_INDEX_NODATA,
        )

        # Calculate crop yield value by multiplying yield index by crop value per hectare
        LOGGER.debug(
            "Calculating crop yield value by multiplying yield index by crop value per hectare"
        )
        pygeoprocessing.raster_map(
            op=lambda y, v: y * v,
            rasters=[
                file_registry["annual_crop_yield_index"],
                file_registry[_CROP_VALUE_FIELD],
            ],
            target_path=file_registry["annual_crop_yield_value"],
            target_dtype=np.float32,
            target_nodata=_INDEX_NODATA,
        )

        # Calculate yield attribution rasters
        for yield_type in ["annual_crop_yield_index", "annual_crop_yield_value"]:
            # Calculate relative contribution of pollination to crop yield
            LOGGER.debug(
                f"Calculating relative contribution of pollination to {yield_type}"
            )
            pygeoprocessing.raster_map(
                op=relative_contribution_op,
                rasters=[
                    file_registry[yield_type],
                    file_registry[_CROP_POLLINATOR_DEPENDENCE_FIELD],
                    file_registry["pollinator_visitation_index"],
                ],
                target_path=file_registry[f"relative_{yield_type}"],
                target_dtype=np.float32,
                target_nodata=_INDEX_NODATA,
            )

            # Convolve the relative contributions by species alpha and multiply by relative abundance
            for species in scenario_variables["species_list"]:
                LOGGER.debug(f"Convolving relative contributions for species {species}")
                alpha = (
                    scenario_variables["alpha_value"][species]
                    / scenario_variables["landcover_mean_pixel_size"]
                )
                pygeoprocessing.convolve_2d(
                    (file_registry[f"relative_{yield_type}"], 1),
                    (file_registry["kernel_[ALPHA]", f"{alpha:.6f}"], 1),
                    file_registry[
                        f"convolved_relative_{yield_type}_[SPECIES]", species
                    ],
                    ignore_nodata_and_edges=True,
                    mask_nodata=True,
                    normalize_kernel=False,
                )

                # Multiply species pollinator supply by convoluted relative yield
                LOGGER.debug(
                    f"Multiplying species pollinator supply (landscape score) by convoluted relative yield for species {species}"
                )
                raster_size_multiplier = (
                    scenario_variables["pixel_area_ha"]
                    if yield_type == "annual_crop_yield_value"
                    else 1
                )
                pygeoprocessing.raster_map(
                    op=lambda ps, conv_ry: ps * conv_ry * raster_size_multiplier,
                    rasters=[
                        file_registry["pollinator_supply_[SPECIES]", species],
                        file_registry[
                            f"convolved_relative_{yield_type}_[SPECIES]", species
                        ],
                    ],
                    target_path=file_registry[
                        f"attributed_{yield_type}_[SPECIES]", species
                    ],
                    target_dtype=np.float32,
                    target_nodata=_INDEX_NODATA,
                )

            # Sum across species to get total attributed yield index and value
            LOGGER.debug(f"Summing across species to get total attributed {yield_type}")
            pygeoprocessing.raster_calculator(
                [
                    (file_registry[f"attributed_{yield_type}_[SPECIES]", species], 1)
                    for species in scenario_variables["species_list"]
                ],
                natcap.invest.pollination._sum_arrays,
                file_registry[f"attributed_{yield_type}"],
                gdal.GDT_Float32,
                _INDEX_NODATA,
            )

    # If scenario analysis is not requested, we can exit early after running the
    # baseline model. Otherwise, we continue to process the scenario-specific
    # marginal value calculations.
    if "scenario_analysis" in args and args["scenario_analysis"]:

        # Process the scenario suffix into the results suffix
        args = combine_results_and_scenario_suffixes(args)

        # Set up args, file registry, and scenario variables for scenario analysis
        args, scenario_file_registry, _ = MODEL_SPEC.setup(args)
        scenario_variables = _parse_scenario_variables(args)

        # Set up reclass errors dict for error messages
        reclass_error_details = {
            "raster_name": "LULC",
            "column_name": "lucode",
            "table_name": "Biophysical",
        }

        # Only calculate marginal value rasters if crop yield modeling is requested. Otherwise, we
        # skip this section.
        if "crop_yield" in args and args["crop_yield"]:
            # Calculating change in annual crop yield by change in pollinator visitation (dYield/dPV)
            LOGGER.debug(
                "Calculating change in annual crop yield by change in pollinator visitation (dYield/dPV)"
            )
            pygeoprocessing.raster_map(
                op=delta_yield_op,
                rasters=[
                    file_registry[_CROP_POLLINATOR_DEPENDENCE_FIELD],
                    file_registry[_HALF_SATURATION_FIELD],
                    file_registry["pollinator_visitation_index"],
                ],
                target_path=scenario_file_registry[
                    "d_annual_crop_yield_index_d_pollinator_visitation"
                ],
                target_dtype=np.float32,
                target_nodata=_INDEX_NODATA,
            )
            # QUESTION Should we mask out any crop areas that are removed between baseline and scenario?
            # QUESTION How do we deal with crop areas that are added in the scenario that weren't in the baseline?

            # Calculating change in annual crop yield value by change in pollinator visitation (dYield/dPV)
            LOGGER.debug(
                "Calculating change in annual crop yield value by change in pollinator visitation (dYieldValue/dPV)"
            )
            pygeoprocessing.raster_map(
                op=lambda x, y: x * y,
                rasters=[
                    scenario_file_registry[
                        "d_annual_crop_yield_index_d_pollinator_visitation"
                    ],
                    file_registry[_CROP_VALUE_FIELD],
                ],
                target_path=scenario_file_registry[
                    "d_annual_crop_yield_value_d_pollinator_visitation"
                ],
                target_dtype=np.float32,
                target_nodata=_INDEX_NODATA,
            )

        # Reclassify landcover to seasonal floral resources
        for season in scenario_variables["season_list"]:
            LOGGER.debug(
                f"Reclassifying landcover to {season} floral abundance (scenario)"
            )
            natcap.invest.utils.reclassify_raster(
                (args["scenario_landcover_raster_path"], 1),
                scenario_variables["landcover_floral_resources"][season],
                scenario_file_registry[
                    "relative_floral_abundance_index_[SEASON]", season
                ],
                gdal.GDT_Float32,
                _INDEX_NODATA,
                reclass_error_details,
            )

        # Reclassify landcover to nesting substrates
        scenario_variables["nesting_substrate_index_path"] = {}
        for substrate in scenario_variables["substrate_list"]:
            LOGGER.debug(f"Reclassifying landcover to {substrate} nesting (scenario)")
            scenario_variables["nesting_substrate_index_path"][substrate] = (
                scenario_file_registry["nesting_substrate_index_[SUBSTRATE]", substrate]
            )
            natcap.invest.utils.reclassify_raster(
                (args["scenario_landcover_raster_path"], 1),
                scenario_variables["landcover_substrate_index"][substrate],
                scenario_file_registry[
                    "nesting_substrate_index_[SUBSTRATE]", substrate
                ],
                gdal.GDT_Float32,
                _INDEX_NODATA,
                reclass_error_details,
            )

        # Iterate through species
        for species in scenario_variables["species_list"]:
            # Calculate habitat nesting per species
            natcap.invest.pollination._calculate_habitat_nesting_index(
                scenario_variables["nesting_substrate_index_path"],
                scenario_variables["species_substrate_index"][species],
                scenario_file_registry["habitat_nesting_index_[SPECIES]", species],
            )

            # Calculate seasonal floral abundance indices
            for season in scenario_variables["season_list"]:
                LOGGER.debug(
                    f"Calculating seasonal forage for {species} in {season} (scenario)"
                )
                natcap.invest.pollination._multiply_by_scalar(
                    scenario_file_registry[
                        "relative_floral_abundance_index_[SEASON]", season
                    ],
                    scenario_variables["species_foraging_activity"][(species, season)],
                    scenario_file_registry[
                        "foraged_flowers_index_[SPECIES]_[SEASON]", species, season
                    ],
                )

            # Calculate scenario local foraging effectiveness
            LOGGER.debug(
                f"Calculating local foraging effectiveness for {species} (scenario)"
            )
            pygeoprocessing.raster_calculator(
                [
                    (
                        scenario_file_registry[
                            "foraged_flowers_index_[SPECIES]_[SEASON]", species, season
                        ],
                        1,
                    )
                    for season in scenario_variables["season_list"]
                ],
                natcap.invest.pollination._sum_arrays,
                scenario_file_registry[
                    "local_foraging_effectiveness_[SPECIES]", species
                ],
                gdal.GDT_Float32,
                _INDEX_NODATA,
            )

            # Calculate d_pollinator_supply for each species
            LOGGER.debug(f"Calculating change in pollinator supply for {species}")

            alpha = (
                scenario_variables["alpha_value"][species]
                / scenario_variables["landcover_mean_pixel_size"]
            )
            delta_pollinator_landscape_score_op = _DeltaPollinatorLandscapeScoreOp(
                scenario_variables["species_abundance"][species],
                file_registry["kernel_[ALPHA]", f"{alpha:.6f}"],
            )
            pygeoprocessing.raster_map(
                op=delta_pollinator_landscape_score_op,
                rasters=[
                    file_registry[
                        "habitat_nesting_density_[SPECIES]", species
                    ],  # baseline
                    file_registry["floral_resources_[SPECIES]", species],  # baseline
                    scenario_file_registry[
                        "local_foraging_effectiveness_[SPECIES]", species
                    ],  # baseline
                    scenario_file_registry[
                        "local_foraging_effectiveness_[SPECIES]", species
                    ],  # scenario
                    file_registry[
                        "habitat_nesting_index_[SPECIES]", species
                    ],  # baseline
                    scenario_file_registry[
                        "habitat_nesting_index_[SPECIES]", species
                    ],  # scenario
                ],
                target_path=scenario_file_registry[
                    "d_pollinator_supply_[SPECIES]", species
                ],
                target_dtype=np.float32,
                target_nodata=_INDEX_NODATA,
            )

            # If crop yield modeling is not requested, skip the following calculations.
            if "crop_yield" not in args or not args["crop_yield"]:
                continue

            # NOTE Insertion point for creating species-level d_annual_crop_yield_d_pollinator_landscape_score
            for yield_type in ["annual_crop_yield_index", "annual_crop_yield_value"]:

                # Calculating change in yield by change in pollinator supply (landscape score; d_pollinator_supply)
                LOGGER.debug(
                    f"Calculating change in {yield_type} by change in pollinator supply (landscape score) function for {species}"
                )
                pygeoprocessing.convolve_2d(
                    (
                        scenario_file_registry[
                            f"d_{yield_type}_d_pollinator_visitation"
                        ],
                        1,
                    ),
                    (file_registry["kernel_[ALPHA]", f"{alpha:.6f}"], 1),
                    scenario_file_registry[
                        f"d_{yield_type}_d_pollinator_supply_[SPECIES]", species
                    ],
                    ignore_nodata_and_edges=True,
                    normalize_kernel=False,
                    target_nodata=_INDEX_NODATA,
                )

                # Calculating change in yield based on change in pollinator supply (landscape score)
                LOGGER.debug(
                    f"Calculating change in {yield_type} by change in pollinator supply (landscape score) for species {species}"
                )
                pygeoprocessing.raster_map(
                    op=lambda x, y: x * y,
                    rasters=[
                        scenario_file_registry[
                            f"d_{yield_type}_d_pollinator_supply_[SPECIES]", species
                        ],
                        scenario_file_registry[
                            "d_pollinator_supply_[SPECIES]", species
                        ],
                    ],
                    target_path=scenario_file_registry[
                        f"d_{yield_type}_[SPECIES]", species
                    ],
                    target_dtype=np.float32,
                    target_nodata=_INDEX_NODATA,
                )

        # Calculate total change in pollinator supply (or landscape score, in Eric's terms)
        LOGGER.debug(
            "Calculating total change in pollinator supply (landscape score) by summing across species"
        )
        pygeoprocessing.raster_calculator(
            [
                (scenario_file_registry["d_pollinator_supply_[SPECIES]", species], 1)
                for species in scenario_variables["species_list"]
            ],
            natcap.invest.pollination._sum_arrays,
            scenario_file_registry["d_pollinator_supply_index"],
            gdal.GDT_Float32,
            _INDEX_NODATA,
        )

        # Run crop yield calculations if requested.
        if "crop_yield" in args and args["crop_yield"]:
            for yield_type in ["annual_crop_yield_index", "annual_crop_yield_value"]:
                # Calculate total change in crop yield-related rasters
                LOGGER.debug(
                    f"Calculating total change in {yield_type} by summing across species-specific contributions"
                )
                pygeoprocessing.raster_calculator(
                    [
                        (
                            scenario_file_registry[
                                f"d_{yield_type}_[SPECIES]", species
                            ],
                            1,
                        )
                        for species in scenario_variables["species_list"]
                    ],
                    natcap.invest.pollination._sum_arrays,
                    scenario_file_registry[f"d_{yield_type}"],
                    gdal.GDT_Float32,
                    _INDEX_NODATA,
                )


def _parse_scenario_variables(args):
    """Parse scenario variables from args, and perform any necessary validation or processing.

    Args:
        args (dict): dictionary of model arguments, as per InVEST requirements. See model spec for details.
    Returns:
        dict: scenario variables, with any necessary processing applied.
    """
    # Add farm_vector_path to args to avoid issues with parsing scenario variables
    args["farm_vector_path"] = args.get("farm_vector_path")
    scenario_variables = natcap.invest.pollination._parse_scenario_variables(args)

    # Create reclass dicts for each of the yield-related fields, mapping LULC codes to the appropriate values from the biophysical table
    landcover_biophysical_df = MODEL_SPEC.get_input(
        "landcover_biophysical_table_path"
    ).get_validated_dataframe(args["landcover_biophysical_table_path"], args=args)

    for field in _EXPECTED_BIOPHYSICAL_YIELD_HEADERS:
        scenario_variables[field] = collections.defaultdict(dict)
        for landcover_id, row in landcover_biophysical_df.iterrows():
            scenario_variables[field][landcover_id] = row[field]

    # Get landcover mean pixel size for convolution kernel calculation
    landcover_raster_info = pygeoprocessing.get_raster_info(
        args["landcover_raster_path"]
    )
    landcover_pixel_size_tuple = landcover_raster_info["pixel_size"]
    try:
        landcover_mean_pixel_size = natcap.invest.utils.mean_pixel_size_and_area(
            landcover_pixel_size_tuple
        )[0]
    except ValueError:
        landcover_mean_pixel_size = np.min(np.absolute(landcover_pixel_size_tuple))
        LOGGER.debug(
            "Land Cover Raster has unequal x, y pixel sizes: "
            f"{landcover_pixel_size_tuple}. Using"
            f"{landcover_mean_pixel_size} as the mean pixel size."
        )

    scenario_variables["landcover_mean_pixel_size"] = landcover_mean_pixel_size

    return scenario_variables


def _create_valid_mask(*arrays, nodata_value=_INDEX_NODATA):
    """Create a boolean mask of valid (non-nodata) pixels across all input arrays."""
    return np.all(
        tuple(
            ~pygeoprocessing.array_equals_nodata(array, nodata_value)
            for array in arrays
        ),
        axis=0,
    )


def yield_op(
    crop_pollinator_dependence_array: np.ndarray,
    half_saturation_coefficient_array: np.ndarray,
    pollinator_visitation_array: np.ndarray,
) -> np.ndarray:
    # NOTE (1 + k) is an adjustment factor to ensure that yield is 1 when v = 1
    """Calculate yield = (1 - cd) + (1 + k) * cd * v / (k + v)
    where:
        cd = crop dependence on pollinators
        k = half saturation coefficient
        v = Pollinator Visitation

    Parameters:
        crop_pollinator_dependence_array (numpy.ndarray): array of crop dependence on pollinators values
        half_saturation_coefficient_array (numpy.ndarray): array of half saturation coefficient values
        pollinator_visitation_array (numpy.ndarray): array of pollinator visitation values

    Returns:
        numpy.ndarray: array of yield values
    """

    result = np.empty_like(crop_pollinator_dependence_array)
    result[:] = _INDEX_NODATA
    denominator = result.copy()

    valid_mask = _create_valid_mask(
        crop_pollinator_dependence_array,
        half_saturation_coefficient_array,
        pollinator_visitation_array,
    )

    k = half_saturation_coefficient_array[valid_mask]
    v = pollinator_visitation_array[valid_mask]

    # Check denominator for zeros and remove from valid mask
    denominator[valid_mask] = k + v
    valid_mask &= denominator != 0

    cd = crop_pollinator_dependence_array[valid_mask]
    k = half_saturation_coefficient_array[valid_mask]
    v = pollinator_visitation_array[valid_mask]
    d = denominator[valid_mask]

    result[valid_mask] = (1 - cd) + (1 + k) * cd * v / d

    return result


def relative_contribution_op(
    crop_yield_array: np.ndarray,
    crop_pollinator_dependence_array: np.ndarray,
    pollinator_visitation_array: np.ndarray,
) -> np.ndarray:
    """Calculate relative contribution of pollination to crop yield.
    relative_contribution = (y * cd) / v
    where:
        y = crop yield
        cd = crop dependence on pollinators
        v = Pollinator Visitation

    Parameters:
        crop_yield_array (numpy.ndarray): array of crop yield values
        crop_pollinator_dependence_array (numpy.ndarray): array of crop dependence on pollinators values
        pollinator_visitation_array (numpy.ndarray): array of pollinator visitation values

    Returns:
        numpy.ndarray: array of relative contribution values
    """

    result = np.empty_like(crop_yield_array)
    result[:] = _INDEX_NODATA

    valid_mask = _create_valid_mask(
        crop_yield_array,
        crop_pollinator_dependence_array,
        pollinator_visitation_array,
    )

    valid_mask &= pollinator_visitation_array != 0  # Avoid division by zero

    y = crop_yield_array[valid_mask]
    cd = crop_pollinator_dependence_array[valid_mask]
    v = pollinator_visitation_array[valid_mask]

    result[valid_mask] = (y * cd) / v

    return result


class _DeltaPollinatorLandscapeScoreOp(object):
    """Calculate dpls/dphab =
    d_forage * nesting_density + d_nest * forage + (d_nest + d_forage) * center of convolution window.
    """

    def __init__(self, species_abundance: float, kernel_path: str):
        """Initialize the delta pollinator landscape score operator.

        Parameters:
            species_abundance (float): value to use in `__call__` when
                calculating pollinator abundance.
            kernel_path (str): path to the convolution kernel raster,
                used to extract the mid-kernel weight for use in the
                marginal value calculation.

        Returns:
            None.
        """
        self.species_abundance = species_abundance

        # Calculate mid-kernel weight for later use in marginal value calculation
        kernel_array = pygeoprocessing.raster_to_numpy_array(str(kernel_path))
        self.mid_kernel_weight = np.max(kernel_array)
        kernel_array = None  # dereference kernel array to free memory

    def __call__(
        self,
        habitat_nesting_density_baseline_array,
        floral_resources_baseline_array,
        local_foraging_effectiveness_baseline_array,
        local_foraging_effectiveness_scenario_array,
        habitat_nesting_index_baseline_array,
        habitat_nesting_index_scenario_array,
    ):
        """Calculate [
            (d_forage * nesting_density) +
            (d_nesting * foraged_resources) +
            (d_nesting + d_forage) * mid_kernel_weight
        ] * species_abundance

        where, for a given species:
            d_forage = change in pixel's floral foraging resources (scenario - baseline)
            nesting_density = nesting density within foraging radius
            d_nesting = change in pixel's habitat nesting suitability (scenario - baseline)
            foraged_resources = foraged floral resources around the pixel in the baseline scenario

        dpls/dphab = (d_forage * nest_scape) + (d_nest * forage) + (d_nest + d_forage) *center of convolution window

        """
        result = np.empty_like(floral_resources_baseline_array)
        result[:] = _INDEX_NODATA
        valid_mask = floral_resources_baseline_array != _INDEX_NODATA

        d_forage = (
            local_foraging_effectiveness_scenario_array[valid_mask]
            - local_foraging_effectiveness_baseline_array[valid_mask]
        )
        nesting_density = habitat_nesting_density_baseline_array[valid_mask]
        d_nesting = (
            habitat_nesting_index_scenario_array[valid_mask]
            - habitat_nesting_index_baseline_array[valid_mask]
        )
        foraged_resources = floral_resources_baseline_array[valid_mask]

        result[valid_mask] = (
            (d_forage * nesting_density)
            + (d_nesting * foraged_resources)
            + (d_nesting + d_forage) * self.mid_kernel_weight
        ) * self.species_abundance

        return result


def delta_yield_op(
    scenario_crop_pollinator_dependence_array,
    scenario_half_saturation_coefficient_array,
    baseline_pollinator_visitation_array,
):
    """Calculate dYield_dPV = k * cd * (1+k) / ((v+k) * (v+k))
    where:
        cd = crop dependence on pollinators
        k = half saturation coefficient
        v = Pollinator Visitation
    """
    # QUESTION Update this to respond to changes in (i.e. delta_) crop pollinator dependence, half saturation, and crop value?
    # QUESTION Currently it is only responsive to changes in pollinator visitation.
    # QUESTION Basically, what happens if you change the crop type as well as the adjacent habitat?

    result = np.empty_like(scenario_crop_pollinator_dependence_array)
    result[:] = _INDEX_NODATA
    denominator = result.copy()

    k = scenario_half_saturation_coefficient_array
    v = baseline_pollinator_visitation_array

    # Check denominator for zeros and remove from valid mask
    denominator = (v + k) * (v + k)
    valid_mask = denominator != 0

    cd = scenario_crop_pollinator_dependence_array[valid_mask]
    k = scenario_half_saturation_coefficient_array[valid_mask]
    v = baseline_pollinator_visitation_array[valid_mask]
    d = denominator[valid_mask]

    result[valid_mask] = (k * cd * (1 + k)) / d
    return result


def combine_results_and_scenario_suffixes(args):
    output_args = args.copy()
    if "scenario_suffix" not in output_args or not output_args["scenario_suffix"]:
        output_args["scenario_suffix"] = "scenario"
    if "results_suffix" not in output_args or not output_args["results_suffix"]:
        output_args["results_suffix"] = output_args["scenario_suffix"]
    else:
        output_args["results_suffix"] = (
            f"{output_args['scenario_suffix']}_{output_args['results_suffix']}"
        )
    return output_args


@validation.invest_validator
def validate(args, limit_to=None):
    return validation.validate(args, MODEL_SPEC)
