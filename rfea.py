'''
NAME:           rfea.py
AUTHOR:         swjtang
DATE:           29 Apr 2021
DESCRIPTION:    A toolbox of functions related to energy analyzer analysis.
------------------------------------------------------------------------------
to reload module:
import importlib
importlib.reload(<module>)
------------------------------------------------------------------------------
'''
import h5py
import importlib
import numpy as np
import re
import scipy
from scipy.optimize import curve_fit
from matplotlib import pyplot as plt

import lib.find_multiref_phase as fmp
import lib.fname_tds as fn
import lib.read_lapd_data as rd
import lib.toolbox as tbx
import lib.spikes as spike


class DataRFEA(object):
    def __init__(self, fid, nsteps, nshots, res, ch_volt=0, ch_curr=3,
                 ch_bdot=None, ch_bint=None):
        self.fid = fid
        self.fname = fn.fname_tds(fid, old=0)
        self.nsteps = nsteps
        self.nshots = nshots
        self.res = res

        # Channel info
        self.ch_volt = ch_volt
        self.ch_curr = ch_curr
        self.ch_bdot = ch_bdot or ch_bint

        # Flag to determine if input channel is B-integrated
        if ch_bint is not None:
            self.f_bint = 1
        else:
            self.f_bint = 0

        # Store parameter data arrays
        self.volt = None
        self.time = None
        self.xpos = None
        self.ypos = None
        self.tarr = None         # time array for plotting Ti/Vp

        # Store dataset parameters
        self.nt = None
        self.dt = None
        self.t1, self.t2 = None, None    # [px] Area of interest
        self.bt1, self.bt2 = None, None  # [px] B-int bounds for Xcorrelation

    def set_time(self, t1=None, t2=None, bt1=None, bt2=None):
        if t1 is not None:
            self.t1 = t1
        if t2 is not None:
            self.t2 = t2
        if bt1 is not None:
            self.bt1 = bt1
        if bt2 is not None:
            self.bt2 = bt2

    ''' ----------------------------------------------------------------------
        GET DATA FUNCTIONS
    --------------------------------------------------------------------------
    '''
    def get_volt(self, quiet=0):
        print("** Running method: get_volt")
        if self.ch_volt is not None:
            dataset = rd.read_lapd_data(
                self.fname, nsteps=self.nsteps, nshots=self.nshots,
                rchan=[self.ch_volt], rshot=[1], quiet=quiet)

            # dataset output is Arr[nt, shot, chan, step]
            data = np.transpose(dataset['data'], (0, 3, 1, 2))

            # x100 = (50x from voltmeter, x2 from 1M/50 Ohm digitizer mismatch)
            self.volt = np.mean(data[10000:35000, :, 0, 0]*100, axis=0)
        return self.volt

    def get_dataset(self, quiet=0):
        print("** Running method: get_dataset")
        dataset = rd.read_lapd_data(
            self.fname, nsteps=self.nsteps, nshots=self.nshots,
            rchan=[self.ch_curr, self.ch_bdot], quiet=quiet)#, rshot=[0,1])

        datatemp = dataset['data']
        self.nt = datatemp.shape[0]
        data = np.transpose(datatemp, (0, 3, 1, 2))
        if self.ch_curr < self.ch_bdot:
            curr = data[..., 0]
            bdot = data[..., 1]
        else:
            bdot = data[..., 0]
            curr = data[..., 1]

        self.time = dataset['time']
        self.dt = dataset['dt']
        self.xpos = dataset['x']
        self.ypos = dataset['y']
        return curr, bdot

    def mean_current(self, curr):
        return np.mean(curr, axis=2)/self.res * 1e6    # [uA]

    def plot_IV(self, volt, curr, times=None):
        if times is None:
            times = [15000, 17500, 20000, 25000, 30000]

        # IV response
        tbx.prefig(xlabel='Peak pulse voltage [V]', ylabel='Current [$\mu$A]')
        for tt in times:
            plt.plot(volt, curr[tt, :], label='{0:.2f} ms'.format(
                self.mstime(tt, start=5)))
        plt.legend(fontsize=20)
        plt.title('Average IV response, NO conditional averaging, {0} shots'.
                  format(self.nshots), fontsize=20)
        tbx.savefig('./img/{0}-average-IV-response.png'.format(self.fid))

        # IV derivative
        tbx.prefig(xlabel='Peak pulse voltage [V]', ylabel='-dI/dV')
        for tt in times:
            deriv = IVderiv(curr[tt, :], nwindow=51)
            plt.plot(volt, deriv, label='{0:.2f} ms'.format(
                self.mstime(tt, start=5)))
        plt.legend(fontsize=20)
        plt.title('Average IV-deriv, NO conditional averaging, {0} shots'.
                  format(self.nshots), fontsize=20)
        tbx.savefig('./img/{0}-average-IV-deriv.png'.format(self.fid))

    ''' ----------------------------------------------------------------------
        ION TEMPERATURE (Ti) MANIPULATION
    --------------------------------------------------------------------------
    '''
    def calc_Ti_arr(self, volt, curr, dt=1, ca=0, **kwargs):
        ''' ---------------------------------------------------------
        Calculate Ti and Vp from an array of RFEA IV curves
        INPUTS:   volt = np.array of voltage data
                  curr = np.array of current data
        OPTIONAL: dt   = Number of indices to skip
        '''
        if ca != 0:
            t1, t2 = 0, curr.shape[0]
            tarr = np.arange(t1, t2, dt)
            self.tarr = np.arange(self.t1+t1, self.t1+t2, dt)
        else:
            t1 = self.t1 or 0
            t2 = self.t2 or curr.shape[0]
            tarr = np.arange(t1, t2, dt)
            self.tarr = tarr

        ntarr = tarr.shape[0]

        # Define arrays
        Ti = np.empty(ntarr)
        Vp = np.empty(ntarr)
        errTi = np.empty(ntarr)

        for ii in range(ntarr):
            tt = tarr[ii]
            tbx.progress_bar([ii], [ntarr])
            Vp[ii], Ti[ii], errTi[ii] = self.find_Ti_exp(
                volt, curr[tt, :]/self.res, plot=0, save=0, **kwargs)
        return Ti, Vp, errTi

    def plot_TiVp(self, Ti, Vp, ca=0):
        tt = np.array([self.mstime(tt) for tt in self.tarr])

        text = 'conditional average, exponential fit'
        if ca == 1:
            text = 'YES '+text
            svname = 'yes-condavg'
        else:
            text = 'NO '+text
            svname = 'no-condavg'

        tbx.prefig(xlabel='time [ms]', ylabel='$T_i$ [eV]')
        plt.title('{0} $T_i$ vs time, {1} (all times)'.format(self.fid,
                  text), fontsize=25)
        # plt.fill_between(tt, tbx.smooth(self.Ti-self.errTi, nwindow=51),
        #     tbx.smooth(self.Ti+self.errTi, nwindow=51), alpha=0.2)
        plt.plot(tt, tbx.smooth(Ti, nwindow=51))
        tbx.savefig('./img/{0}-{1}-Ti-vs-time.png'.format(self.fid, svname))

        tbx.prefig(xlabel='time [ms]', ylabel='$V_p$ [V]')
        plt.title('{0} $V_p$ vs time, {1} (all times)'.format(self.fid,
                  text), fontsize=25)
        plt.plot(tt, Vp)
        tbx.savefig('./img/{0}-{1}-Vp-vs-time.png'.format(self.fid, svname))

    ''' ----------------------------------------------------------------------
        B-DOT MANIPULATION
    --------------------------------------------------------------------------
    '''
    def integrate_bdot(self, bdot, axis=0):
        # Method to integrate the B-dot signal. Also checks if input is B-int.
        if bdot is None:
            print("** Running method: get_dataset...")
            self.get_dataset(quiet=1)

        if self.f_bint == 1:
            print("** Input B-data is already integrated. Saving bint...")
            bint = bdot
        else:
            bint = tbx.filter_bint(bdot, axis=axis)
        return bint

    def plot_bint_range(self, bint, step=0, shot=0):
        # Plot function to show the bounded region of integrated B used for
        # conditional averaging
        # INPUTS: bdata = 1D data array
        if bint is None:
            print("** Running method: integrate_bdot...")
            self.integrate_bdot()

        bdata = bint[:, step, shot]

        tbx.prefig(xlabel='time [px]', ylabel='B-int')
        plt.plot(bdata)
        bt1 = self.bt1 or 0
        bt2 = self.bt2 or len(bdata)

        plt.plot([bt1, bt1], [np.min(bdata), np.max(bdata)], 'orange')
        plt.plot([bt2, bt2], [np.min(bdata), np.max(bdata)], 'orange')
        plt.title('integrated B, step={0}, shot={1}'.format(step, shot),
                  fontsize=20)
        tbx.savefig('./img/{0}-condavg-range.png'.format(self.fid))

    def plot_bint_shift(self, bint, curr=None, step=0, shot=0):
        # Plots the reference bint/current with a test shot
        bref = bint[self.bt1:self.bt2, 0, 0]
        bdata = bint[self.bt1:self.bt2, step, shot]
        xlag = fmp.lagtime(bref, bdata)['xlag']
        if xlag is not None:
            tbx.prefig()
            plt.title('integrated B signals', fontsize=25)
            plt.plot(bref, label='reference')
            plt.plot(bdata, label='original')
            plt.plot(np.roll(bdata, -xlag), label='shift')
            plt.legend(fontsize=20)

            if curr is not None:
                curr0 = self.curr[self.bt1:self.bt2, 0, 0]
                curr1 = self.curr[self.bt1:self.bt2, step, shot]
                tbx.prefig()
                plt.title('current signals', fontsize=25)
                plt.plot(curr0, label='reference')
                plt.plot(np.roll(curr1, -xlag), label='shift')
                plt.legend(fontsize=20)
            else:
                print("** curr = None, current not plotted")

    ''' ----------------------------------------------------------------------
        CONDITIONAL AVERAGING ROUTINE
    --------------------------------------------------------------------------
    '''
    def condavg(self, bint, curr, bref=None, ref=None):
        ''' ------------------------------------------------------------------
        Conditionally avarage shift of RFEA current data.
        INPUTS:   data    = np.array with the data to be conditionally
                             averaged.
                  bdata   = np.array with the phase information (usually bdot)
                  nsteps  = Number of steps in the voltage sweep
                  nshots  = Number of shots for each step in the voltage sweep
                  trange  = Time range to store conditionally averaged data.
                  btrange = Time range of the conditional averaging (bdot)
        OPTIONAL: ref = [step, shot] number of the reference shot
                  bref = Inputs a reference shot for conditional averaging
        '''
        # Set default values
        if (self.t1 is None) and (self.t2 is None):
            self.t1, self.t2 = 0, curr.shape[0]
            print("** condavg t1, t2 undefined, setting defaults t1, t2 = {0},"
                  " {1}". format(self.t1, self.t2))
        if (self.bt1 is None) and (self.bt2 is None):
            self.bt1, self.bt2 = self.t1, self.t2
            print("** condavg bt1, bt2 undefined, setting defaults bt1, bt2 ="
                  " {0}, {1}". format(self.bt1, self.bt2))
        if ref is None:
            ref = [0, 0]

        # Current array, shifted in phase
        curr_arr = np.zeros((self.t2-self.t1, self.nsteps, self.nshots))
        # Array shows number of shots skipped because cross-correlation fails
        skip_arr = np.zeros(self.nsteps)

        # Determine the reference shot in bdata
        if bref is None:
            bref = bint[self.bt1:self.bt2, ref[0], ref[1]]

        for step in range(self.nsteps):
            skips = 0
            for shot in range(self.nshots):
                tbx.progress_bar([step, shot], [self.nsteps, self.nshots],
                                 ['nsteps', 'nshots'])
                bsig = bint[self.bt1:self.bt2, step, shot]
                xlag = fmp.lagtime(bref, bsig, quiet=1, threshold=0.8)['xlag']

                if xlag is not None:
                    curr_arr[:, step, shot] = np.roll(curr[self.t1:self.t2,
                                                      step, shot], -xlag)
                else:
                    skips += 1
            skip_arr[step] = skips

        # Calculates factor so that mean_curr takes mean of shots not skipped
        factor = [self.nshots/(self.nshots - ii) for ii in skip_arr]
        mean_condavg_curr = np.mean(curr_arr, axis=2) * factor

        return mean_condavg_curr, skip_arr, bref

    ''' ----------------------------------------------------------------------
        GENERAL DATA ANALYSIS FUNCTIONS
    --------------------------------------------------------------------------
    '''
    def mstime(self, ind, start=5, off=0):
        # Determines the actual time (in ms) from the start of the discharge
        # start: [ms] the start time of the trigger (recorded)
        # off:   [px] the offset of the analyzed slice of data
        return self.time[int(ind)+off]*1e3 + start


def IVderiv(curr, scale=1, nwindow=51, nwindow2=None, polyn=3, **kwargs):
    # Calculates the current derivative -dI/dV
    smoo1 = tbx.smooth(curr, nwindow=nwindow, polyn=polyn, **kwargs)
    grad1 = -np.gradient(smoo1)
    if nwindow2 is not None:
        smoo2 = tbx.smooth(grad1, nwindow=nwindow2, polyn=polyn, **kwargs)
        return smoo2*scale
    else:
        return grad1*scale


def find_Ti(xx, yy, plot=0, width=40, xmax=0, xoff=10):
    # Find Ti from curve fitting of the decaying part of -dI/dV
    # xoff = offset used for curve fitting gaussian
    # Assume the peak of the gaussian is the maximum point
    iimax = np.argmax(yy)

    # Try searching for local minima (end point of curve fit)
    if xmax == 0:
        try_arr = np.arange(iimax, len(yy), width)
        prev = try_arr[0]
        for iitry in try_arr[1:]:
            iimin = iimax + np.argmin(yy[iimax:iitry])
            if (iimin < iitry) & (iimin == prev):
                break
            prev = iimin
    else:
        iimin = len(yy)-1

    if plot != 0:
        plt.plot(xx[max(iimax-xoff, 0)], yy[max(iimax-xoff, 0)], 'o')
        plt.plot(xx[iimin], yy[iimin], 'o')

    def gauss_func(x, a, b, c, x0):
        return a * np.exp(-b * (np.sqrt(x)-np.sqrt(x0))**2) + c

    guess = [yy[iimax]-yy[iimin], 1, yy[iimin], xx[iimax]]

    popt, pcov = curve_fit(gauss_func, xx[max(iimax-xoff, 0):iimin],
                           yy[max(iimax-xoff, 0):iimin], p0=guess)

    # Plot the points if desired
    if plot != 0:
        plt.plot(xx[max(iimax-xoff, 0):iimin],
                 gauss_func(xx[max(iimax-xoff, 0):iimin], *popt), '--')

    # Returns full width of gaussian; b = 1/kT = 1/(2*sigma^2)
    return popt, pcov  # (1/np.sqrt(*popt[1]))


def find_Ti_exp(volt, curr, startpx=100, endpx=100, plot=0, mstime=0,
                fid=None, save=0):
    '''
    # Finds Ti from an exponential plot; The curve starts from some max
    # value then decays exponentially.
    startpx = number of pixels to count at the start to determine max value
    endpx   = number of pixels to count at the end to determine min value
    '''
    # Smooth the curve
    temp = tbx.smooth(curr, nwindow=11)

    # Take gradient to find peak of dI/dV, thats where to cut the exp fit off
    gradient = np.gradient(tbx.smooth(temp, nwindow=51))
    argmin = np.argmin(gradient)

    # Determine start and end values of the curve
    vstart = np.mean(temp[:startpx])
    vend = np.mean(temp[-endpx:])

    def exp_func(x, a, b, c, x0):
        return a * np.exp(-(x-x0)/b) + c

    guess = [vstart-vend, 2, vend, volt[argmin]]
    bound_down = [0.1*(vstart-vend), 0, vend-vstart, volt[0]]
    bound_up = [+np.inf, 50, vstart, volt[-1]]
    try:
        popt, pcov = curve_fit(exp_func, volt[argmin:], temp[argmin:],
                               p0=guess, bounds=(bound_down, bound_up))
    except:
        return None, None, None

    Vp = volt[np.argmin(abs([vstart for ii in volt] - exp_func(
        volt, *popt)))]
    Ti = popt[1]
    Ti_err = np.sqrt(np.diag(pcov))[1]

    if plot != 0:
        tbx.prefig(xlabel='peak pulse potential [V]',
                   ylabel='current [$\mu$A]')
        plt.plot(volt, temp, label='{0} ms'.format(mstime))
        plt.title('{0} exponential fit, t = {1:.2f} ms'.format(fid, mstime),
                  fontsize=20)
        plt.plot(volt[argmin:], temp[argmin:])
        plt.plot(volt, [vstart for ii in volt], '--')
        plt.plot(volt, [vend for ii in volt], '--')
        plt.plot(volt[argmin-20:], exp_func(volt[argmin-20:], *popt), '--')
        plt.ylim(np.min(temp)*0.96, np.max(temp)*1.04)
        if save == 1:
            tbx.savefig('./img/{0}-currpeak-exp-fit-{1:.2f}ms.png'.format(
                        fid, mstime))

    return Vp, Ti, Ti_err


def select_peaks(step_arr, argtime_arr, peakcurr_arr, step=0, trange=None):
    # Given arrays of (1) step number, (2) argtime and (3) peak current
    # corresponding to each peak, returns peaks that fulfil input
    # conditions.
    # Set default trange
    if trange is None:
        trange = [0, 1]

    ind = np.where((argtime_arr > trange[0]) & (argtime_arr <= trange[1]) &
                   (step_arr == step))
    return peakcurr_arr[ind]


''' ----------------------------------------------------------------------
    PLOTTING FUNCTIONS
--------------------------------------------------------------------------
'''
def plot_volt(volt):
    tbx.prefig(xlabel='step', ylabel='voltage [V]')
    plt.plot(volt)


def plot_IVderiv(volt, curr, xoff=0, yoff=0, nwindow=51, polyn=3,
                 **kwargs):
    # Plots the current derivative -dI/dV (if input is deriv do a regular
    # plot)
    plt.plot(volt + xoff, IVderiv(curr, nwindow=nwindow, polyn=polyn) +
             yoff, **kwargs)


def browse_data(data, x=None, y=None, step=0, shot=0, chan=0, trange=None):
    # Browses the data and returns the selected trange
    # Set default trange
    if trange is None:
        trange = [0, -1]

    t1 = trange[0]
    t2 = np.min([data.shape[0], trange[1]])
    tbx.prefig(xlabel='time [px]', ylabel='magnitude')

    if (x is None) and (y is None):
        temp = data[:, step, shot, chan]
        plt.title("step = {0}, shot = {1}, chan = {2}, trange = [{3},"
                  " {4}]". format(step, shot, chan, trange[0], trange[1]),
                  fontsize=20)
    else:
        temp = data[:, x, y, step, shot, chan]
        plt.title('(x, y) = ({0:.2f}, {1:.2f}), step = {2}, shot = {3}, '
                  ' chan = {4}, trange = [{5}, {6}]'.format(x, y, step, shot,
                  chan, trange[0], trange[1]), fontsize=20)

    plt.plot(temp)
    plt.plot([t1, t1], [np.min(temp), np.max(temp)], 'orange')
    plt.plot([t2, t2], [np.min(temp), np.max(temp)], 'orange')

    return t1, t2


''' ----------------------------------------------------------------------
    NAMING FUNCTIONS
--------------------------------------------------------------------------
'''
def fname_gen(series, date='2021-01-28', folder='/data/swjtang/RFEA/',
              ch=2):
    # Plots the current derivative -dI/dV
    return '{0}{1}/C{2}{3}.txt'.format(folder, date, ch, series)
