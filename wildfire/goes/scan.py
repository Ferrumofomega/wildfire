"""Wrapper for a GOES-16/17 satellite image."""
import logging
import os

import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

from . import downloader, utilities

LOCAL_DIRECTORY = "downloaded_data"

_logger = logging.getLogger(__name__)


class GoesScan:  # pylint: disable=too-few-public-methods
    """Wrapper around a single channel-region-time satellite scan from GOES.

    Attributes
    ----------
    region : str
        Must be in the set (F, M1, M2, C). Defined in the `filepath`.
    channel : int
        Must be between 1 - 16. Defined in the `filepath`.
    satellite : str
        Must be either "goes16" or "goes17". Defined in the `filepath`.
    started_at_utc : datetime.datetime
        Scan start datetime. Defined in the `filepath`.
    filepath : str
        Local filepath to the desired data set. This is the same as the s3 key for the
        same data set.
        e.g. ABI-L1b-RadM/2019/300/20/
        OR_ABI-L1b-RadM1-M6C14_G17_s20193002048275_e20193002048332_c20193002048405.nc
    local_directory : str
        Local directory in which to look for the `filepath`.
    dataset : xr.Dataset
        The data found at `{local_directory}/{filepath}` after postprocessing throught
        the `_process()` method.
    """

    def __init__(self, filepath, local_directory=LOCAL_DIRECTORY):
        """Initialize.

        Parameters
        ----------
        filepath : str
            Local filepath to the desired data set. This is the same as the s3 key for the
            same data set.
            e.g. ABI-L1b-RadM/2019/300/20/
            OR_ABI-L1b-RadM1-M6C14_G17_s20193002048275_e20193002048332_c20193002048405.nc
        local_directory : str
            Optional. Local directory in which to look for the `filepath`. Defaults to
            "downloaded_data".
        """
        region, channel, satellite, started_at_utc = utilities.parse_filepath(
            filepath=filepath
        )
        self.region = region
        self.channel = channel
        self.satellite = satellite
        self.started_at_utc = started_at_utc
        self.filepath = filepath
        self.local_directory = local_directory
        self.dataset = self._get()

    def plot(self, axis=None, **imshow_kwargs):
        """Plot the reflectance factor or the brightness temperature based on channel."""
        if axis is None:
            _, axis = plt.subplots(figsize=(12, 12))
            axis.axis("off")

        title_info = f"{self.channel}\n{self.started_at_utc} UTC"
        if self.channel in range(1, 6):
            axis.set_title(f"Reflectance Factor Band \n{title_info}", fontsize=20)
            axis.imshow(self.dataset.reflectance_factor, **imshow_kwargs)
            return axis

        axis.set_title(f"Brightness Temperature Band \n{title_info}", fontsize=20)
        axis.imshow(self.dataset.brightness_temperature, **imshow_kwargs)
        return axis

    def _get(self):
        """Get the satellite scan.

        Check `local_directory` for the scan. If it's not there, then download it from
        Amazon S3.

        Returns
        -------
        xr.Dataset
        """
        scan = self._check_local()
        if scan is None:
            file_path = downloader.download_scan(
                s3_bucket=self.satellite,
                s3_key=self.filepath,
                local_directory=self.local_directory,
            )
            scan = xr.open_dataset(file_path)
        return self._process(dataset=scan)

    def _process(self, dataset):
        """Process scan.

        1. Translate spectral radiance to reflectance factor, based on channel
        2. Translate spectral radiance to brightness temperature, based on channel
        """
        _logger.info("Processing scan...")
        dataset = self._calculate_reflectance_factor(dataset=dataset)
        dataset = self._calculate_brightness_temperature(dataset=dataset)
        return dataset

    def _check_local(self):
        """Check the `local_directory` for the scan defined by `file_path`.

        Returns
        -------
        xr.Dataset | None
            If found locally, return it, else return None.
        """
        local_filepath = utilities.build_local_path(
            local_directory=self.local_directory,
            filepath=self.filepath,
            satellite=self.satellite,
        )
        if os.path.exists(local_filepath):
            _logger.info("Reading scan from local path %s", local_filepath)
            return xr.open_dataset(local_filepath)
        return None

    @staticmethod
    def _calculate_reflectance_factor(dataset):
        return dataset.assign(reflectance_factor=lambda ds: ds.Rad * ds.kappa0)

    @staticmethod
    def _calculate_brightness_temperature(dataset):
        return dataset.assign(
            brightness_temperature=lambda ds: (
                (ds.planck_fk2 / (np.log((ds.planck_fk1 / ds.Rad) + 1)) - ds.planck_bc1)
                / ds.planck_bc2
            )
        )

    def _to_2km_resolution(self):
        raise NotImplementedError

    def _filter_bad_pixels(self):
        raise NotImplementedError