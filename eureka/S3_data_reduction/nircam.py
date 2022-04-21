# NIRCam specific rountines go here
import numpy as np
from astropy.io import fits
import astreus.xarrayIO as xrio
from . import sigrej, background

# Read FITS file from JWST's NIRCam instrument
def read(filename, data, meta):
    '''Reads single FITS file from JWST's NIRCam instrument.

    Parameters
    ----------
    filename:   str
        Single filename to read
    data:   Xarray Dataset
        The Dataset object in which the fits data will be stored
    meta:   MetaClass
        The metadata object

    Returns
    -------
    data: DataClass
        The updated data object with the fits data stored inside

    Notes
    -----
    History:

    - November 2012 Kevin Stevenson
        Initial version
    - May 2021 KBS
        Updated for NIRCam
    - July 2021
        Moved bjdtdb into here
    - Apr 20, 2022 Kevin Stevenson
        Convert to using Xarray Dataset
    '''
    assert isinstance(filename, str)

    hdulist = fits.open(filename)

    # Load master and science headers
    data.attrs['filename'] = filename
    data.attrs['mhdr']     = hdulist[0].header
    data.attrs['shdr']     = hdulist['SCI',1].header
    data.attrs['intstart'] = data.attrs['mhdr']['INTSTART']
    data.attrs['intend']   = data.attrs['mhdr']['INTEND']

    sci     = hdulist['SCI',1].data
    err     = hdulist['ERR',1].data
    dq      = hdulist['DQ',1].data
    v0      = hdulist['VAR_RNOISE',1].data
    wave_2d = hdulist['WAVELENGTH',1].data
    int_times = hdulist['INT_TIMES',1].data[data.attrs['intstart']-1:data.attrs['intend']]

    # Record integration mid-times in BJD_TDB
    time = int_times['int_mid_BJD_TDB']

    # Record units
    flux_units  = data.attrs['shdr']['BUNIT']
    time_units = 'BJD_TDB'
    wave_units = 'microns'

    data['flux'] = xrio.makeFluxLikeDA( sci, time, flux_units, time_units, name='flux')
    data['err']  = xrio.makeFluxLikeDA( err, time, flux_units, time_units, name='err')
    data['dq']   = xrio.makeFluxLikeDA(  dq, time,     "None", time_units, name='dq')
    data['v0']   = xrio.makeFluxLikeDA(  v0, time, flux_units, time_units, name='v0')
    #data['wave'] = xrio.makeWaveLikeDA(wave[0], wave[0], wave_units, wave_units, name='wave')
    data['wave_2d'] = (['y','x'], wave_2d)
    data['wave_2d'].attrs['wave_units'] = wave_units
    #data.attrs['wave_2d'] = wave_2d

    return data, meta

def flag_bg(data, meta):
    '''Outlier rejection of sky background along time axis.

    Parameters
    ----------
    data:   Xarray Dataset
        The Dataset object containing the fits data
    meta:   MetaClass
        The metadata object

    Returns
    -------
    data:   Xarray Dataset
        The updated data object with outlier background pixels flagged.
    '''
    y1, y2, bg_thresh = meta.bg_y1, meta.bg_y2, meta.bg_thresh

    bgdata1 = data.flux[:,  :y1]
    bgmask1 = data.mask[:,  :y1]
    bgdata2 = data.flux[:,y2:  ]
    bgmask2 = data.mask[:,y2:  ]
    import time
    bgerr1  = np.median(data.err[:,  :y1])
    bgerr2  = np.median(data.err[:,y2:  ])
    estsig1 = [bgerr1 for j in range(len(bg_thresh))]
    estsig2 = [bgerr2 for j in range(len(bg_thresh))]

    data['mask'][:,  :y1] = sigrej.sigrej(bgdata1, bg_thresh, bgmask1, estsig1)
    data['mask'][:,y2:  ] = sigrej.sigrej(bgdata2, bg_thresh, bgmask2, estsig2)

    return data


def fit_bg(dataim, datamask, n, meta, isplots=False):
    '''Fit for a non-uniform background.
    '''

    bg, mask = background.fitbg(dataim, meta, datamask, meta.bg_y1, meta.bg_y2, deg=meta.bg_deg,
                                threshold=meta.p3thresh, isrotate=2, isplots=isplots)
    return (bg, mask, n)
