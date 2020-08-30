'''
NAME:           get_bdot_calib.py
AUTHOR:         swjtang
DATE:           15 Aug 2019
DESCRIPTION:    generates NA, the effective area of the probe (# turns * area) from the
                B-dot calibration files (different for Bx, By, Bz)
'''
import numpy as np
import struct         # for binary structure files
import os, glob       # file path manipulation
import re             # regular expressions

#custom programs
from lib.toolbox         import *
from lib.fname_tds       import fname_tds
from lib.binary_to_ascii import b2a

# fname_ext = os.path.basename(fff)
# fname     = os.path.splitext(fname_ext)[0]
#print([os.path.basename(item) for item in flist])

# checks if the calibration files exist for a given probe
def probe_check(probe, fdir):
    flist = [os.path.basename(item) for item in glob.glob(fdir+'*.DAT')] 
    calib = [probe+item for item in ['_BX.DAT', '_BY.DAT', '_BZ.DAT']]
    exist_check = [item in flist for item in calib]
    
    chreq = ', '.join([a for (a,b) in zip(['Bx','By','Bz'], exist_check) if not b])
    if len(chreq) > 0: 
        print('!!! Missing calibration for Bdot #{0}: ({1})'.format(probe, chreq))
        return(None)
    else:
        return(calib)

# List all the probes in the directory. Assumes filenames in the format ""<probe>_BX.DAT".
def get_probe_list(fdir):
    names = [os.path.basename(item) for item in glob.glob(fdir+'*.DAT')]
    print('List of probes with calibrations: ', \
          np.unique(np.array([re.split('_', item)[0] for item in names])))
    
#########################################################################################
from matplotlib import animation, cm, pyplot as plt

# plots all 3 calibration curves
def calib_3plots(probeid, data1, data2, data3):
    fig, ax1 = plt.subplots(figsize=(10,11.25/2))
    fig.subplots_adjust(hspace=0.05)

    lm1= ax1.plot(data1['freq']/1e3, data1['logmag'], label='Bx LOGMAG')
    lm2= ax1.plot(data2['freq']/1e3, data2['logmag'], label='By LOGMAG')
    lm3= ax1.plot(data3['freq']/1e3, data3['logmag'], label='Bz LOGMAG')
    ax1.set_title('Calibration plots for Bdot probe #{0}'. format(probeid), fontsize=18)
    ax1.set_ylabel('LOGMAG [dB]', fontsize=16)
    
    ax3 = ax1.twinx()
    ph1 = ax3.plot(data1['freq']/1e3, data1['phase'], linestyle=':', label='Bx PHASE')
    ph2 = ax3.plot(data2['freq']/1e3, data2['phase'], linestyle=':', label='By PHASE')
    ph3 = ax3.plot(data3['freq']/1e3, data3['phase'], linestyle=':', label='Bz PHASE')
    ax3.set_ylabel('PHASE (degrees)', fontsize=16)
    lns = lm1+lm2+lm3+ph1+ph2+ph3
    labs = [l.get_label() for l in lns]
    ax1.legend(lns, labs, loc=0, fontsize=16)
    
    ax1.tick_params(axis='both', labelsize=20)
    ax3.tick_params(axis='both', labelsize=20)

#########################################################################################
import scipy.constants as const
from scipy.optimize import curve_fit

### PROBE AREA CALIBRATION
def area_calib(data, g=10, r=5.4e-2, label='', quiet=0, debug=0):
    # the data needs to contain frequency and logmag data in dict
    freq   = data['freq']     # [Hz]
    logmag = data['logmag']   # [dB]

    ratioAR = [10**(ii/20) for ii in logmag]

    def fline(x, AA, BB): # define straight line function y=f(x)
        return AA*x + BB

    # the gradient contains the area value
    AA, BB = curve_fit(fline, 2*np.pi*freq, ratioAR)[0] # fit to data x, y

    mu = const.mu_0
    area = AA / (32 * (4/5)**1.5 *g *mu / r)
    qprint(quiet, label+'Effective area = (Probe area * turns), NA = {0:.4f} [cm^2]'.format(area*1e4))

    if debug != 0:
        plt.figure(figsize=(8,4.5))
        plt.plot(freq/1e3, ratioAR)
        plt.plot(freq/1e3, fline(freq, AA, BB))
        plt.xlabel('Frequency [kHz]', fontsize=16)
        plt.ylabel('magnitude A/R', fontsize=16)
        plt.tick_params(axis='both', labelsize=20)
        plt.legend(['original', 'best fit line'], fontsize=16)

    return(area)   # returns in m^2
######################################################################################

def get_bdot_calib(probeid='1', fdir='/home/swjtang/bdotcalib/', quiet=0, debug=0, output=0, ch=2):
    if probeid in ['11']: ch=1   # exception list of probe calibrations in channel 1 of VNA

    if quiet == 0: get_probe_list(fdir)
    bnames = probe_check(probeid, fdir)

    data1 = b2a(fdir+bnames[0], ch=ch, output=output)
    data2 = b2a(fdir+bnames[1], ch=ch, output=output)
    data3 = b2a(fdir+bnames[2], ch=ch, output=output)

    if debug != 0: calib_3plots(probeid, data1, data2, data3)

    areas = np.empty(3)
    for ii in range(3):
        data  = [data1, data2, data3]
        label = ['BX', 'BY', 'BZ']
        areas[ii] = area_calib(data[ii], label=label[ii]+' ', quiet=quiet)
    
    temp = {
        'probeid': probeid,
        'areas'  : areas
    }
    return(temp)