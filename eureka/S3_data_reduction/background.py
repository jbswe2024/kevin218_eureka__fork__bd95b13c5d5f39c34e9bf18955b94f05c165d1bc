import numpy as np
from tqdm import tqdm
import ccdproc as ccdp
from astropy import units
import multiprocessing as mp
from scipy.special import erf
import matplotlib.pyplot as plt
from astropy.nddata import CCDData
from astropy.modeling.models import Gaussian1D
from astropy.stats import SigmaClip, sigma_clip
from astropy.modeling.models import custom_model
from astropy.modeling.fitting import LevMarLSQFitter
from photutils import MMMBackground, MedianBackground, Background2D
from astropy.io import fits
import os

from ..lib import clipping

__all__ = ['BGsubtraction', 'fitbg', 'fitbg2', 'fitbg3']

def BGsubtraction(data, meta, log, isplots):
    """Does background subtraction using inst.fit_bg & background.fitbg

    Parameters
    ----------
    data:   DataClass
        Data object containing data, uncertainty, and variance arrays in units of MJy/sr or DN/s.
    meta:   MetaClass
        The metadata object.
    log:    logedit.Logedit
        The open log in which notes from this step can be added.
    isplots:    int
        The amount of plots saved; set in ecf.

    Returns
    -------
    data:   DataClass
        Data object containing background subtracted data.

    Notes
    ------
    History:

    - Dec 10, 2021 Taylor Bell
        Edited to pass the full DataClass object into inst.fit_bg
    """
    
    # Load instrument module
    if meta.inst == 'miri':
        from . import miri as inst
    elif meta.inst == 'nircam':
        from . import nircam as inst
    elif meta.inst == 'nirspec':
        from . import nirspec as inst
    elif meta.inst == 'niriss':
        raise ValueError('NIRISS observations are currently unsupported!')
    elif meta.inst == 'wfc3':
        from . import wfc3 as inst
    else:
        raise ValueError('Unknown instrument {}'.format(meta.inst))

    # Write background
    def writeBG(arg):
        bg_data, bg_mask, n = arg
        data.subbg[n] = bg_data
        data.submask[n] = bg_mask
        return

    # Compute background for each integration
    log.writelog('  Performing background subtraction', mute=(not meta.verbose))
    data.subbg = np.zeros((data.subdata.shape))
    if meta.ncpu == 1:
        # Only 1 CPU
        iterfn = range(meta.int_start,meta.n_int)
        if meta.verbose:
            iterfn = tqdm(iterfn)
        for n in iterfn:
            # Fit sky background with out-of-spectra data
            if meta.inst in ['niriss', 'wfc3']:
                writeBG(inst.fit_bg(data, meta, n, isplots))
            else:
                dataim = data.subdata[n]
                datamask = data.submask[n]
                writeBG(inst.fit_bg(dataim, datamask, n, meta, isplots))
    else:
        # Multiple CPUs
        pool = mp.Pool(meta.ncpu)
        args_list = []

        # Todo, convert WFC3/NIRISS fit_bg to only accept individual frames (see nircam and below for example)
        if meta.inst in ['niriss', 'wfc3']:
            for n in range(meta.int_start,meta.n_int):
                args_list.append((data, meta, n, isplots))
            jobs = [pool.apply_async(func=inst.fit_bg, args=(*args,), callback=writeBG) for args in args_list]
        else: 
            jobs = [pool.apply_async(func=inst.fit_bg, args=(data.subdata[n], data.submask[n], n, meta, isplots,), callback=writeBG) for n in range(meta.int_start,meta.n_int)]
        pool.close()
        iterfn = jobs
        if meta.verbose:
            iterfn = tqdm(iterfn)
        for job in iterfn:
            res = job.get()

    # 9.  Background subtraction
    # Perform background subtraction
    data.subdata -= data.subbg
    
    if hasattr(meta, 'save_bgsub') and meta.save_bgsub:
        log.writelog('  Saving background subtracted FITS file', mute=(not meta.verbose))
        new_filename = data.filename.split(os.sep)[-1]
        new_folder = os.path.join(meta.outputdir, 'bgsub_FITS')
        if not os.path.isdir(new_folder):
            os.mkdir(new_folder)
        new_filename = os.path.join(new_folder, new_filename)
        with fits.open(data.filename) as file:
            file["SCI"].data = data.subdata
            file.writeto(new_filename)

    return data

# STEP 3: Fit sky background with out-of-spectra data
def fitbg(dataim, meta, mask, x1, x2, deg=1, threshold=5, isrotate=False, isplots=0):
    '''Fit sky background with out-of-spectra data.

    Parameters
    ----------
    dataim: ndarray
        The data array
    meta:   MetaClass
        The metadata object.
    mask:   ndarray
        A mask array
    x1:     ndarray
    x2:     ndarray
    deg:    int, optional
        Polynomial order for column-by-column background subtraction
        Default is 1.
    threshold:  int, optional
        Sigma threshold for outlier rejection during background subtraction.
        Defaullt is 5.
    isrotate:   bool, optional
        Default is False.
    isplots:    int, optional
        The amount of plots saved; set in ecf. Default is 0.

    Notes
    ------
    History:

    - May 2013
        Removed [::-1] for LDSS3

    - Feb 2014
        Modified x1 and x2 to allow for arrays
    '''
    # Assume x is the spatial direction and y is the wavelength direction
    # Otherwise, rotate array
    if isrotate == 1:
        dataim = dataim[::-1].T
        mask   = mask[::-1].T
    elif isrotate == 2:
        dataim = dataim.T
        mask   = mask.T

    #Convert x1 and x2 to array, if need be
    ny, nx   = np.shape(dataim)
    if type(x1) == int or type(x1) == np.int64:
        x1 = np.zeros(ny,dtype=int)+x1
    if type(x2) == int or type(x2) == np.int64:
        x2 = np.zeros(ny,dtype=int)+x2

    if deg < 0:
        # Calculate median background of entire frame
        # Assumes all x1 and x2 values are the same
        submask = np.concatenate((  mask[:,    :x1[0]].T,  mask[:,x2[0]+1:nx].T)).T
        subdata = np.concatenate((dataim[:,    :x1[0]].T,dataim[:,x2[0]+1:nx].T)).T
        bg      = np.zeros((ny,nx)) + np.median(subdata[np.where(submask)])
    elif deg == None :
        # No background subtraction
        bg      = np.zeros((ny,nx))
    else:
        degs = np.ones(ny)*deg
        # Initiate background image with zeros
        bg      = np.zeros((ny,nx))
        # Fit polynomial to each column
        for j in range(ny):
            nobadpixels = False
            # Create x indices for background sections of frame
            xvals    = np.concatenate((range(x1[j]), range(x2[j]+1,nx))).astype(int)
            # If too few good pixels then average
            if (np.sum(mask[j,:x1[j]]) < deg) or (np.sum(mask[j,x2[j]+1:nx]) < deg):
                degs[j] = 0
            while (nobadpixels == False):
                try:
                    goodxvals = xvals[np.where(mask[j,xvals])]
                except:
                    print("****Warning: Background subtraction failed!****")
                    print(j)
                    print(xvals)
                    print(np.where(mask[j,xvals]))
                    return
                dataslice = dataim[j,goodxvals]
                # Check for at least 1 good x value
                if len(goodxvals) == 0:
                    nobadpixels = True      #exit while loop
                    #Use coefficients from previous row
                else:
                    # Fit along spatial direction with a polynomial of degree 'deg'
                    coeffs    = np.polyfit(goodxvals, dataslice, deg=degs[j])
                    # Evaluate model at goodexvals
                    model     = np.polyval(coeffs, goodxvals)
                    # Calculate residuals and number of sigma from the model
                    residuals = dataslice - model
                    # Simple standard deviation (faster but prone to missing scanned background stars)
                    #stdres = np.std(residuals)
                    # Median Absolute Deviation (slower but more robust)
                    #stdres  = np.median(np.abs(np.ediff1d(residuals)))
                    # Mean Absolute Deviation (good comprimise)
                    stdres  = np.mean(np.abs(np.ediff1d(residuals)))
                    if stdres == 0:
                        stdres = np.inf
                    stdevs    = np.abs(residuals) / stdres
                    # Find worst data point
                    loc       = np.argmax(stdevs)
                    # Mask data point if > threshold
                    if stdevs[loc] > threshold:
                        mask[j,goodxvals[loc]] = 0
                    else:
                        nobadpixels = True      #exit while loop

            # Evaluate background model at all points, write model to background image
            if len(goodxvals) != 0:
                bg[j] = np.polyval(coeffs, range(nx))
                if isplots >= 6:
                    plt.figure(3601)
                    plt.clf()
                    plt.title(str(j))
                    plt.plot(goodxvals, dataslice, 'bo')
                    plt.plot(range(nx), bg[j], 'g-')
                    plt.savefig(meta.outputdir + 'figs/Fig6_BG_'+str(j)+'.png')
                    plt.pause(0.01)

    if isrotate == 1:
        bg   = (bg.T)[::-1]
        mask = (mask.T)[::-1]
    elif isrotate == 2:
        bg   = (bg.T)
        mask = (mask.T)

    return bg, mask

# STEP 3: Fit sky background with out-of-spectra data
def fitbg2(dataim, meta, mask, bgmask, deg=1, threshold=5, isrotate=False, isplots=0):
    '''Fit sky background with out-of-spectra data.

    fitbg2 uses bgmask, a mask for the background region which enables fitting more complex
    background regions than simply above or below a given distance from the trace. This will
    help mask the 2nd and 3rd orders of NIRISS.

    Parameters
    ----------
    dataim: ndarray
        The data array
    meta:   MetaClass
        The metadata object.
    mask:   ndarray
        A mask array
    bgmask: ndarray
        A background mask array.
    deg:    int, optional
        Polynomial order for column-by-column background subtraction.
        Default is 1.
    threshold:  int, optional
        Sigma threshold for outlier rejection during background subtraction.
        Default is 5.
    isrotate:   bool, optional
        Default is False.
    isplots:    int, optional
        The amount of plots saved; set in ecf. Default is 0.

    Notes
    ------
    History:

    - September 2016 Kevin Stevenson
        Initial version
    '''
    # Assume x is the spatial direction and y is the wavelength direction
    # Otherwise, rotate array
    if isrotate == 1:
        dataim = dataim[::-1].T
        mask   = mask[::-1].T
        bgmask = bgmask[::-1].T

    elif isrotate == 2:
        dataim = dataim.T
        mask   = mask.T
        bgmask = bgmask.T

    # Initiate background image with zeros
    ny, nx  = np.shape(dataim)
    bg      = np.zeros((ny,nx))

    if deg < 0:
        # Calculate median background of entire frame
        bg  += np.median(dataim[np.where(mask2)])

    elif deg == None :
        # No background subtraction
        pass
    else:
        degs = np.ones(ny)*deg
        # Fit polynomial to each column
        for j in tqdm(range(ny)):
            nobadpixels = False
            # Create x indices for background sections of frame
            xvals   = np.where(bgmask[j] == 1)[0]
            # If too few good pixels on either half of detector then compute average
            if (np.sum(bgmask[j,:int(nx/2)]) < deg) or (np.sum(bgmask[j,int(nx/2):nx]) < deg):
                degs[j] = 0
            while (nobadpixels == False):
                try:
                    goodxvals = xvals[np.where(bgmask[j,xvals])]
                except:
                    print('column: ', j, 'xvals: ', xvals)
                    print(np.where(mask[j,xvals]))
                    return
                dataslice = dataim[j,goodxvals]
                # Check for at least 1 good x value
                if len(goodxvals) == 0:
                    nobadpixels = True      #exit while loop
                    #Use coefficients from previous row

                else:
                    # Fit along spatial direction with a polynomial of degree 'deg'
                    coeffs    = np.polyfit(goodxvals, dataslice, deg=degs[j])
                    # Evaluate model at goodexvals
                    model     = np.polyval(coeffs, goodxvals)

                    #model = smooth.smooth(dataslice, window_len=window_len, window=windowtype)
                    #model = sps.medfilt(dataslice, window_len)
                    if isplots == 6:
                        plt.figure(3601)
                        plt.clf()
                        plt.title(str(j))
                        plt.plot(goodxvals, dataslice, 'bo')
                        plt.plot(goodxvals, model, 'g-')
                        plt.savefig(meta.outputdir + 'figs/Fig6_BG_'+str(j)+'.png')
                        plt.pause(0.01)

                    # Calculate residuals
                    residuals = dataslice - model

                    # Find worst data point
                    loc         = np.argmax(np.abs(residuals))
                    # Calculate standard deviation of points excluding worst point
                    ind = np.arange(0,len(residuals),1)
                    ind = np.delete(ind, loc)
                    stdres = np.std(residuals[ind])
                    
                    if stdres == 0:
                        stdres = np.inf
                    # Calculate number of sigma from the model
                    stdevs    = np.abs(residuals) / stdres
                    #print(stdevs)

                    # Mask data point if > threshold
                    if stdevs[loc] > threshold:
                        bgmask[j,goodxvals[loc]] = 0
                    else:
                        nobadpixels = True      #exit while loop

                    if isplots == 6:
                        plt.figure(3601)
                        plt.clf()
                        plt.title(str(j))
                        plt.plot(goodxvals, dataslice, 'bo')
                        plt.plot(goodxvals, model, 'g-')
                        plt.pause(0.01)
                        plt.show()

            # Evaluate background model at all points, write model to background image
            if len(goodxvals) != 0:
                bg[j] = np.polyval(coeffs, range(nx))
                #bg[j] = np.interp(range(nx), goodxvals, model)

    if isrotate == 1:
        bg      = (bg.T)[::-1]
        mask    = (mask.T)[::-1]
        bgmask  = (bgmask.T)[::-1]
    elif isrotate == 2:
        bg      = (bg.T)
        mask    = (mask.T)
        bgmask  = (bgmask.T)

    return bg, bgmask#, mask #,variance



def bkg_sub(img, mask, sigma=5, bkg_estimator='median',
            box=(10,2), filter_size=(1,1)):
    """
    Completes a step for fitting a 2D background
    model.
    
    Parameters
    ----------
    img : np.ndarray
       Single exposure frame.
    mask : np.ndarray
       Mask to remove the orders.
    sigma : float, optional
       Sigma to remove above. Default is 5.
    bkg_estimator : str, optional
       Which type of 2D background model to use.
       Default is `median`.
    box : tuple, optional
       Box size by which to smooth over. Default
       is (10,2) --> prioritizes smoothing by
       column.
    filter_size : tuple, optional
       The window size of the 2D filter to apply to the
       low-resolution background map. Default is (1,1).

    Returns
    -------
    background : np.ndarray
       The modeled background image.
    """
    sigma_clip = SigmaClip(sigma=sigma)
    
    if bkg_estimator.lower()=='mmmbackground':
        bkg = MMMBackground()
    elif bkg_estimator.lower()=='median':
        bkg = MedianBackground()
        
    b = Background2D(img, box,
                     filter_size=filter_size,
                     bkg_estimator=bkg,
                     sigma_clip=sigma_clip, fill_value=0.0,
                     mask=mask)
    return b.background


def fitbg3(data, order_mask, readnoise=11, sigclip=[4,2,3], isplots=0):
    """
    Fit sky background with out-of-spectra data. Optimized to remove
    the 1/f noise in the NIRISS spectra (works in the y-direction).

    Parameters
    ----------
    isplots : bool, optional                                      
       Plots intermediate steps for the background fitting routine.
       Default is False.                                          

    Returns
    -------
    data : object
       data object now contains new attribute `bkg_removed`.
    """

    # Removes cosmic rays
    # Loops through niters cycles to make sure all pesky
    #    cosmic rays are trashed
    rm_crs = np.zeros(data.data.shape)
    bkg_subbed = np.zeros(data.data.shape)

    for i in tqdm(range(len(data.data))):

        ccd = CCDData((data.data[i])*units.electron)
        mask = np.zeros(data.data[i].shape)

        for n in range(len(sigclip)):
            m1  = ccdp.cosmicray_lacosmic(ccd, readnoise=readnoise, sigclip=sigclip[n])
            ccd = CCDData(m1.data*units.electron)
            mask[m1.mask==True]+=1

        rm_crs[i] = m1.data
        rm_crs[i][mask>=1] = np.nan

        rm_crs[i] = clipping.gauss_removal(rm_crs[i], ~order_mask,
                                           linspace=[-200,200]) # removal from background
        rm_crs[i] = clipping.gauss_removal(rm_crs[i], order_mask,
                                           linspace=[-10,10], where='order') # removal from order
        

        b1 = bkg_sub(rm_crs[i], 
                     order_mask,
                     bkg_estimator='median', sigma=4, box=(10,5), filter_size=(2,2))
        b2 = bkg_sub(rm_crs[i]-b1, 
                     order_mask,
                     sigma=3,
                     bkg_estimator='median')
        
        bkg_subbed[i] = (rm_crs[i]-b1)-b2

        
    data.bkg_removed = bkg_subbed

    return data
