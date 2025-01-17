"""Utilities combining goes level 1 data and wildfire modelling."""
import datetime
import json
import logging
import os

import matplotlib.pyplot as plt
import numpy as np

from wildfire import multiprocessing
from wildfire.data import goes_level_1
from . import model as threshold_model

WILDFIRE_FILENAME = "wildfires_{satellite}_{region}_s{start}_e{end}_c{created}.json"
DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

_logger = logging.getLogger(__name__)


def find_wildfires(filepaths, pbs=False, **cluster_kwargs):
    """Find wildfires in goes scans found at `filepaths`.

    Organize filepaths into groups of scans and parse for wildfires.

    Parameters
    ----------
    filepaths : list of str

    Returns
    -------
    list of dict
        List of wildfires where each wildfire is of the form:
        {
            "scan_time_utc": str,
            "region": str,
            "satellite": str
        }
        which can be used to uniquely identify the set of 16 files that comprise a
        complete scan.
    """
    _logger.info("Grouping files into scans...")
    scan_filepaths = goes_level_1.utilities.group_filepaths_into_scans(
        filepaths=filepaths
    )

    _logger.info(
        "Processing %d scans with %d workers...", len(scan_filepaths), os.cpu_count()
    )
    wildfires = multiprocessing.map_function(
        function=parse_scan_for_wildfire,
        function_args=[scan_filepaths],
        pbs=pbs,
        **cluster_kwargs,
    )
    wildfires = list(filter(None, wildfires))

    _logger.info("Found %d wildfires.", len(wildfires))
    return wildfires


def parse_scan_for_wildfire(filepaths):
    """Determine if scan defined by `filepaths` has a wildfire.

    Parameters
    ----------
    filepaths : list of str
        Must be a set of 16 files, which together define the 16 bands of a complete scan.

    Returns
    -------
    dict | None
        `dict` if scan has wildfire, else `None`, where `dict` is of the form:
        {
            "scan_time_utc": str,
            "region": str,
            "satellite": str
        }
        which can be used to uniquely identify the set of 16 files that comprise a
        complete scan.
    """
    try:
        goes_scan = goes_level_1.scan.read_netcdfs(local_filepaths=filepaths)
    except ValueError as error_message:
        _logger.warning(
            "\nSkipping malformed goes_scan comprised of %s.\nError: %s",
            filepaths,
            error_message,
        )
        return None

    if predict_wildfires(goes_scan=goes_scan).mean() > 0:
        return {
            "scan_time_utc": goes_scan.scan_time_utc.strftime("%Y-%m-%dT%H:%M:%S%f"),
            "region": goes_scan.region,
            "satellite": goes_scan.satellite,
        }
    return None


def get_model_features(goes_scan):
    """Calculate features of the threshold model from a `GoesScan`.

    To do this, the provided `GoesScan` is first rescaled such that all bands are in the
    same spatial resoltuion (namely 500m).

    Parameters
    ----------
    goes_scan : wildfire.data.goes_level_1.GoesScan
        A scan of 16 bands of light over some region on Earth.

    Returns
    -------
    wildfire.models.threshold_model.ModelFeatures
        Namedtuple of features used as input to the `predict` method.
    """
    rescaled_scan = goes_scan.rescale_to_2km()

    with np.errstate(invalid="ignore"):
        is_hot = threshold_model.is_hot_pixel(
            brightness_temperature_3_89=rescaled_scan[
                "band_7"
            ].brightness_temperature.data,
            brightness_temperature_11_19=rescaled_scan[
                "band_14"
            ].brightness_temperature.data,
        )
        is_night = threshold_model.is_night_pixel(
            reflectance_factor_0_64=rescaled_scan["band_2"].reflectance_factor.data,
            reflectance_factor_0_87=rescaled_scan["band_3"].reflectance_factor.data,
        )
        is_water = threshold_model.is_water_pixel(
            reflectance_factor_2_25=rescaled_scan["band_6"].reflectance_factor.data
        )
        is_cloud = threshold_model.is_cloud_pixel(
            reflectance_factor_0_64=rescaled_scan["band_2"].reflectance_factor.data,
            reflectance_factor_0_87=rescaled_scan["band_3"].reflectance_factor.data,
            brightness_temperature_12_27=rescaled_scan[
                "band_15"
            ].brightness_temperature.data,
        )
    return threshold_model.ModelFeatures(
        is_hot=is_hot, is_night=is_night, is_water=is_water, is_cloud=is_cloud,
    )


def predict_wildfires(goes_scan):
    """Get model predictions for wildfire detection for a `GoesScan`.

    Parameters
    ----------
    goes_scan : wildfire.data.goes_level_1.GoesScan

    Returns
    -------
    np.ndarray of bool
        A prediction (True/False) of whether a wildfire is detected at each pixel.
    """
    model_features = get_model_features(goes_scan=goes_scan)
    model_predictions = threshold_model.predict(
        is_hot=model_features.is_hot,
        is_cloud=model_features.is_cloud,
        is_night=model_features.is_night,
        is_water=model_features.is_water,
    )
    return model_predictions


def plot_wildfires(goes_scan):
    """Highlight pixels of a wildfire and plot next to the provided GOES scan.

    Parameters
    ----------
    goes_scan : wildfire.data.goes_level_1.GoesScan

    Returns
    -------
    list of plt.image.AxesImage
    """
    model_predictions = predict_wildfires(goes_scan=goes_scan)

    _, (axis_fire, axis_scan) = plt.subplots(ncols=2, figsize=(20, 8))
    axis_fire.set_title(f"Wildfire Present: {model_predictions.mean() > 0}", fontsize=20)

    image_fire = axis_fire.imshow(model_predictions)
    image_scan = goes_scan["band_7"].plot(axis=axis_scan)

    image_fire.axes.axis("off")
    image_scan.axes.axis("off")
    plt.tight_layout()
    return [image_fire, image_scan]


def label_wildfires(
    scan_filepaths,
    persist_directory,
    satellite,
    region,
    start,
    end,
    pbs=False,
    **cluster_kwargs,
):
    """Create a list of all scans that have wildfires.

    Parameters
    ----------
    scan_filepaths : list of str
    persist_directory : str
    satellite : str
        Must be either "noaa-goes16" or "noaa-goes17".
    region : str
        Must be one of ("M1", "M2", "C", "F")
    start : datetime.datetime
    end : datetime.datetime
    pbs : bool, optional
        Whether or not to launch and parallize using PBS, by default False

    Returns
    -------
    dict
    """
    wildfires = multiprocessing.map_function(
        function=parse_scan_for_wildfire,
        function_args=[scan_filepaths],
        pbs=pbs,
        **cluster_kwargs,
    )
    wildfires = list(filter(None, wildfires))
    _logger.info("Found %d wildfires.", len(wildfires))

    if len(wildfires) > 0:
        wildfires_filepath = os.path.join(
            persist_directory,
            WILDFIRE_FILENAME.format(
                satellite=satellite,
                region=region,
                start=start.strftime(DATETIME_FORMAT),
                end=end.strftime(DATETIME_FORMAT),
                created=datetime.datetime.utcnow().strftime(DATETIME_FORMAT),
            ),
        )
        _logger.info("Persisting wildfires to %s", wildfires_filepath)
        with open(wildfires_filepath, "w+") as buffer:
            json.dump(dict(enumerate(wildfires)), buffer)
    else:
        _logger.info("No wildfires found...")

    return wildfires
