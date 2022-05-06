# NIRCam specific rountines go here
import numpy as np
from astropy.io import fits
from . import sigrej, background


def read(filename, data, meta):
    '''Reads single FITS file from JWST's NIRCam instrument.

    Parameters
    ----------
    filename : str
        Single filename to read.
    data : DataClass
        The data object in which the fits data will stored.
    meta : eureka.lib.readECF.MetaClass
        The metadata object.

    Returns
    -------
    data : DataClass
        The updated data object with the fits data stored inside.
    meta : eureka.lib.readECF.MetaClass
        The updated metadata object.

    Notes
    -----
    History:

    - November 2012 Kevin Stevenson
        Initial version
    - May 2021 KBS
        Updated for NIRCam
    - July 2021
        Moved bjdtdb into here
    '''
    hdulist = fits.open(filename)

    # Load master and science headers
    data.filename = filename
    data.mhdr = hdulist[0].header
    data.shdr = hdulist['SCI', 1].header

    data.intstart = data.mhdr['INTSTART']
    data.intend = data.mhdr['INTEND']

    data.data = hdulist['SCI', 1].data
    data.err = hdulist['ERR', 1].data
    data.dq = hdulist['DQ', 1].data
    data.wave = hdulist['WAVELENGTH', 1].data
    data.v0 = hdulist['VAR_RNOISE', 1].data
    int_times = hdulist['INT_TIMES', 1].data[data.intstart-1:data.intend]

    # Record integration mid-times in BJD_TDB
    data.time = int_times['int_mid_BJD_TDB']
    meta.time_units = 'BJD_TDB'

    return data, meta


def flag_bg(data, meta):
    '''Outlier rejection of sky background along time axis.

    Parameters
    ----------
    data : DataClass
        The data object in which the fits data will stored.
    meta : eureka.lib.readECF.MetaClass
        The metadata object.

    Returns
    -------
    data : DataClass
        The updated data object with outlier background pixels flagged.
    '''
    y1, y2, bg_thresh = meta.bg_y1, meta.bg_y2, meta.bg_thresh

    bgdata1 = data.subdata[:, :y1]
    bgmask1 = data.submask[:, :y1]
    bgdata2 = data.subdata[:, y2:]
    bgmask2 = data.submask[:, y2:]
    bgerr1 = np.median(data.suberr[:, :y1])
    bgerr2 = np.median(data.suberr[:, y2:])
    estsig1 = [bgerr1 for j in range(len(bg_thresh))]
    estsig2 = [bgerr2 for j in range(len(bg_thresh))]

    data.submask[:, :y1] = sigrej.sigrej(bgdata1, bg_thresh, bgmask1, estsig1)
    data.submask[:, y2:] = sigrej.sigrej(bgdata2, bg_thresh, bgmask2, estsig2)

    return data


def fit_bg(dataim, datamask, n, meta, isplots=0):
    """Fit for a non-uniform background.

    Parameters
    ----------
    dataim : ndarray (2D)
        The 2D image array.
    datamask : ndarray (2D)
        An array of which data should be masked.
    n : int
        The current integration.
    meta : eureka.lib.readECF.MetaClass
        The metadata object.
    isplots : int; optional
        The plotting verbosity, by default 0.

    Returns
    -------
    bg : ndarray (2D)
        The fitted background level.
    mask : ndarray (2D)
        The updated mask after background subtraction.
    n : int
        The current integration number.
    """
    bg, mask = background.fitbg(dataim, meta, datamask, meta.bg_y1,
                                meta.bg_y2, deg=meta.bg_deg,
                                threshold=meta.p3thresh, isrotate=2,
                                isplots=isplots)

    return bg, mask, n
