'''
NAME:           rfea.py
AUTHOR:         swjtang
DATE:           28 Jun 2021
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
from matplotlib import animation, pyplot as plt

import lib.find_multiref_phase as fmp
import lib.fname_tds as fn
import lib.read_lapd_data as rd
import lib.toolbox as tbx
import lib.spikes as spike


class params(object):
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

    # Set analysis times
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
    GET DATA METHODS
--------------------------------------------------------------------------
'''
class data():
    def __init__(self, obj):
        self.obj = obj

    # Get voltage step data
    def get_volt(self, quiet=0, rshot=None, **kwargs):
        if rshot is None:
            rshot = [1]
        dataset = rd.read_lapd_data(
            self.obj.fname, nsteps=self.obj.nsteps, nshots=self.obj.nshots,
            rchan=[self.obj.ch_volt], rshot=rshot, quiet=quiet, **kwargs)

        # dataset output is Arr[nt, shot, chan, step]
        data = np.transpose(dataset['data'], (0, 3, 1, 2))

        # x100 = (50x from voltmeter, x2 from 1M/50 Ohm digitizer mismatch)
        self.obj.volt = np.mean(data[10000:35000, :, 0, 0]*100, axis=0)
        return self.obj.volt

    # Get current and bdot data
    def get_dataset(self, quiet=0, **kwargs):
        dataset = rd.read_lapd_data(
            self.obj.fname, nsteps=self.obj.nsteps, nshots=self.obj.nshots,
            rchan=[self.obj.ch_curr, self.obj.ch_bdot], quiet=quiet, **kwargs)

        datatemp = dataset['data']
        self.obj.nt = datatemp.shape[0]
        data = np.transpose(datatemp, (0, 3, 1, 2))
        if self.obj.ch_curr < self.obj.ch_bdot:
            curr = data[..., 0]
            bdot = data[..., 1]
        else:
            bdot = data[..., 0]
            curr = data[..., 1]

        self.obj.time = dataset['time']
        self.obj.dt = dataset['dt'][0]
        self.obj.xpos = dataset['x']
        self.obj.ypos = dataset['y']
        return curr, bdot

    # Get description of datarun
    def get_desc(self, **kwargs):
        dataset = rd.read_lapd_data(
            self.obj.fname, nsteps=1, nshots=1,
            rchan=[0], rshot=[0], quiet=1, **kwargs)
        print(dataset['desc'])

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
            self.obj.tarr = np.arange(self.obj.t1+t1, self.obj.t1+t2, dt)
        else:
            t1 = self.obj.t1 or 0
            t2 = self.obj.t2 or curr.shape[0]
            tarr = np.arange(t1, t2, dt)
            self.obj.tarr = tarr

        ntarr = tarr.shape[0]

        # Define arrays
        Ti = np.empty(ntarr)
        Vp = np.empty(ntarr)
        errTi = np.empty(ntarr)

        for ii in range(ntarr):
            tt = tarr[ii]
            tbx.progress_bar([ii], [ntarr])
            Vp[ii], Ti[ii], errTi[ii] = find_Ti_exp(
                volt, curr[tt, :]/self.obj.res, plot=0, save=0, **kwargs)
        return Ti, Vp, errTi

    def plot_TiVp(self, Ti, Vp, ca=0):
        tt = np.array([self.mstime(tt) for tt in self.obj.tarr])

        text = 'conditional average, exponential fit'
        if ca == 1:
            text = 'YES '+text
            svname = 'yes-condavg'
        else:
            text = 'NO '+text
            svname = 'no-condavg'

        tbx.prefig(xlabel='time [ms]', ylabel='$T_i$ [eV]')
        plt.title('{0} $T_i$ vs time, {1} (all times)'.format(self.obj.fid,
                  text), fontsize=25)
        # plt.fill_between(tt, tbx.smooth(self.obj.Ti-self.obj.errTi,
        #                  nwindow=51),
        # tbx.smooth(self.obj.Ti+self.obj.errTi, nwindow=51), alpha=0.2)
        plt.plot(tt, tbx.smooth(Ti, nwindow=51))
        tbx.savefig('./img/{0}-{1}-Ti-vs-time.png'.format(self.obj.fid,
                                                          svname))

        tbx.prefig(xlabel='time [ms]', ylabel='$V_p$ [V]')
        plt.title('{0} $V_p$ vs time, {1} (all times)'.format(self.obj.fid,
                  text), fontsize=25)
        plt.plot(tt, Vp)
        tbx.savefig('./img/{0}-{1}-Vp-vs-time.png'.format(self.obj.fid,
                                                          svname))

    ''' ----------------------------------------------------------------------
        B-DOT MANIPULATION
    --------------------------------------------------------------------------
    '''
    def integrate_bdot(self, bdot, axis=0):
        # Method to integrate the B-dot signal. Also checks if input is B-int.
        # if bdot is None:
        #     print("** Running method: get_dataset...")
        #     self.obj.get_dataset(quiet=1)
        if self.obj.f_bint == 1:
            print("** Input B-data is already integrated. Saving bint...")
            bint = bdot
        else:
            bint = tbx.bdot.bint(bdot, axis=axis)
        return bint

    def plot_bint_range(self, bint, step=0, shot=0):
        # Plot function to show the bounded region of integrated B used for
        # conditional averaging
        # INPUTS: bdata = 1D data array
        if bint is None:
            print("** Running method: integrate_bdot...")
            self.obj.integrate_bdot()

        bdata = bint[:, step, shot]

        tbx.prefig(xlabel='time [px]', ylabel='B-int')
        plt.plot(bdata)
        bt1 = self.obj.bt1 or 0
        bt2 = self.obj.bt2 or len(bdata)

        plt.plot([bt1, bt1], [np.min(bdata), np.max(bdata)], 'orange')
        plt.plot([bt2, bt2], [np.min(bdata), np.max(bdata)], 'orange')
        plt.title('integrated B, step={0}, shot={1}'.format(step, shot),
                  fontsize=20)
        tbx.savefig('./img/{0}-condavg-range.png'.format(self.obj.fid))

    def plot_bint_shift(self, bint, curr=None, step=0, shot=0):
        # Plots the reference bint/current with a test shot
        bref = bint[self.obj.bt1:self.obj.bt2, 0, 0]
        bdata = bint[self.obj.bt1:self.obj.bt2, step, shot]
        xlag = fmp.lagtime(bref, bdata)['xlag']
        if xlag is not None:
            tbx.prefig()
            plt.title('integrated B signals', fontsize=25)
            plt.plot(bref, label='reference')
            plt.plot(bdata, label='original')
            plt.plot(np.roll(bdata, -xlag), label='shift')
            plt.legend(fontsize=20)

            if curr is not None:
                curr0 = self.obj.curr[self.obj.bt1:self.obj.bt2, 0, 0]
                curr1 = self.obj.curr[self.obj.bt1:self.obj.bt2, step, shot]
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
        if (self.obj.t1 is None) and (self.obj.t2 is None):
            self.obj.t1, self.obj.t2 = 0, curr.shape[0]
            print("** condavg t1, t2 undefined, setting defaults t1, t2 = {0},"
                  " {1}". format(self.obj.t1, self.obj.t2))
        if (self.obj.bt1 is None) and (self.obj.bt2 is None):
            self.obj.bt1, self.obj.bt2 = self.obj.t1, self.obj.t2
            print("** condavg bt1, bt2 undefined, setting defaults bt1, bt2 ="
                  " {0}, {1}". format(self.obj.bt1, self.obj.bt2))
        if ref is None:
            ref = [0, 0]

        # Current array, shifted in phase
        curr_arr = np.zeros((self.obj.t2-self.obj.t1, self.obj.nsteps,
                             self.obj.nshots))
        # Array shows number of shots skipped because cross-correlation fails
        skip_arr = np.zeros(self.obj.nsteps)

        # Determine the reference shot in bdata
        if bref is None:
            bref = bint[self.obj.bt1:self.obj.bt2, ref[0], ref[1]]

        for step in range(self.obj.nsteps):
            skips = 0
            for shot in range(self.obj.nshots):
                tbx.progress_bar([step, shot], [self.obj.nsteps,
                                 self.obj.nshots], ['nsteps', 'nshots'])
                bsig = bint[self.obj.bt1:self.obj.bt2, step, shot]
                xlag = fmp.lagtime(bref, bsig, quiet=1, threshold=0.7)['xlag']

                if xlag is not None:
                    curr_arr[:, step, shot] = np.roll(
                        curr[self.obj.t1:self.obj.t2, step, shot], -xlag)
                else:
                    skips += 1
            skip_arr[step] = skips

        factor = np.zeros(len(skip_arr))
        # Calculates factor so that mean_curr takes mean of shots not skipped
        for ii in range(len(skip_arr)):
            if (self.obj.nshots - skip_arr[ii] > 0):
                factor[ii] = self.obj.nshots/(self.obj.nshots - skip_arr[ii])
            else:
                print(self.obj.nshots, skip_arr[ii])
                print('factor = 0 for step {0}, all shots skipped!'.format(ii))
        mean_condavg_curr = np.mean(curr_arr, axis=2) * factor

        # Calculate rejection rate
        _ = fmp.reject_rate(skip_arr)

        return mean_condavg_curr, bref

    ''' ----------------------------------------------------------------------
        GENERAL DATA ANALYSIS FUNCTIONS
    --------------------------------------------------------------------------
    '''
    def mstime(self, *args, **kwargs):
        return trigtime(self.obj.time, *args, **kwargs)

    def mean_current(self, curr):
        return np.mean(curr, axis=2)/self.obj.res * 1e6    # [uA]

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
                  format(self.obj.nshots), fontsize=20)
        tbx.savefig('./img/{0}-average-IV-response.png'.format(self.obj.fid))

        # IV derivative
        tbx.prefig(xlabel='Peak pulse voltage [V]', ylabel='-dI/dV')
        for tt in times:
            deriv = IVderiv(curr[tt, :], nwindow=51)
            plt.plot(volt, deriv, label='{0:.2f} ms'.format(
                self.mstime(tt, start=5)))
        plt.legend(fontsize=20)
        plt.title('Average IV-deriv, NO conditional averaging, {0} shots'.
                  format(self.obj.nshots), fontsize=20)
        tbx.savefig('./img/{0}-average-IV-deriv.png'.format(self.obj.fid))


''' --------------------------------------------------------------------------
    SINGLE DISTRIBUTION FUNCTION ANALYSIS
------------------------------------------------------------------------------
'''
class dfunc():
    def __init__(self, x, y):
        self.x = x    # Voltage array
        self.y = y    # -dI/dV array

        # Stored values
        self.rms = 0
        self.guess = None
        self.bounds = None

    # Define fit functions ---------------------------------------------------
    @staticmethod
    def onegauss_func(x, x1, a1, b1, x2, a2, b2, c):
        return a1 * np.exp(-((x-x1)/b1)**2) + c

    @staticmethod
    def twogauss_func(x, x1, a1, b1, x2, a2, b2, c):
        return a1 * np.exp(-((x-x1)/b1)**2) +\
               a2 * np.exp(-((x-x2)/b2)**2) + c

    @staticmethod
    def gauss(x, x1, a1, b1, c):
        return a1* np.exp(-((x-x1)/b1)**2) + c


    # Calculate the max noise value
    def update_rms(self, xrange=None):
        if xrange is None:
            xrange=[43, 95]
        ind = np.where((self.x < xrange[0]) | (self.x > xrange[1]))
        self.rms = np.amax(self.y[ind]) 
        return self.rms


    # Set default values if no input is specified ----------------------------
    # Array is for gaussfit (x1, a1, b1, x2, a2, b2, c)
    def set_guess(self):
        a1 = np.amax(self.y)
        argb1 = np.argwhere(self.y > np.amax(self.y)/2)
        b1 = (self.x[argb1[-1]]-self.x[argb1[0]])/(2*np.sqrt(2*np.log(2)))
        a2 = self.update_rms()    # this is self.rms

        guess = [60, a1, b1, 80, a2, 1, 0]    # checkpoint

        # Find peaks and replace if they are found
        peaks, _ = scipy.signal.find_peaks(self.y, height=1.5*a2, 
                                           distance=20, prominence=a2)
        if len(peaks) > 0:
            guess[0] = self.x[peaks[0]]
        if len(peaks) > 1:
            guess[3] = self.x[peaks[1]]
        return guess


    def set_bounds(self):
        _ = self.update_rms()
        return [(50, self.rms, 0, 50, self.rms, 0, -0.5),
                (90, 1.1*np.amax(self.y), 10,
                 90, 1.1*np.amax(self.y), 10, 0.05)]


    # Fitting function for distribution function -----------------------------
    def gaussfit(self, guess=None, bounds=None, onegauss=None, **kwargs):
        # Set default guess and boundaries
        if guess is None:
            guess = self.set_guess()
        if bounds is None:
            bounds = self.set_bounds()

        if onegauss is None:
            fitfunc = self.twogauss_func
        else:
            fitfunc = self.onegauss_func

        try:
            popt, _ = scipy.optimize.curve_fit(fitfunc, self.x, self.y,
                                               p0=guess, bounds=bounds)
            # Calculate least squares for error
            arg = np.argwhere(self.y > self.rms)
            lsq = np.sum((self.y[arg] - fitfunc(self.x[arg], *popt))**2)
            return popt, lsq
        except (RuntimeError, ValueError):
            return None, None


    # Plot components of the distribution function ---------------------------
    def dfplot(self, x, y, popt, lsq, fitfunc, color='red', window=None,
               **kwargs):
        if fitfunc is self.twogauss_func:
            wlabel = 'LSQ = {0:.4f}, '\
                     '$x_1$ = {1:.2f}, $A_1$ = {2:.2f}, $b_1$ = {3:.2f}, '\
                     '$x_2$ = {4:.2f}, $A_2$ = {5:.2f}, $b_2$ = {6:.2f}, '\
                     '$c$ = {7:.2f}'.format(lsq, *popt)
            # A2
            window.plot(x, self.gauss(x, popt[3], popt[4], popt[5], popt[6]),
                        color=color, alpha=0.3) 
        else:
            wlabel = 'LSQ = {0:.4f}, '\
                     '$x_1$ = {1:.2f}, $A_1$ = {2:.2f}, $b_1$ = {3:.2f}, '\
                     '$c$={7:.2f}'.format(lsq, *popt)

        window.plot(x, fitfunc(x, *popt), label=wlabel, color=color)
        # A1
        window.plot(x, self.gauss(x, popt[0], popt[1], popt[2], popt[6]),
                    color=color, alpha=0.3)


    # Multiple function analysis. Plot best curve from least squares. --------
    def bestfit(self, window=None, rec_guess=None):
        # rec_guess = A guess value to be passed to check for better guesses
        popt1, lsq1 = self.gaussfit()
        popt2, lsq2 = self.gaussfit(guess=rec_guess)
        popt3, lsq3 = self.gaussfit(onegauss=1)

        if window is not None:
            window.plot([self.x[0], self.x[-1]], [1.5*self.rms, 1.5*self.rms],
                        '--')

        def check_reject(popt):
            # [x1, a1, b1, x2, a2, b2, c]
            closeness = 1#abs(popt[0]-popt[3])/((popt[0]+popt[3])/2)
            if (closeness < 0.1) or (popt[1] < 1.5*self.rms) or \
               (popt[4] < 1.5*self.rms):
                return 1
            else:
                return None

        popt, lsq = None, 1e6
        if lsq1 is not None:
            if (lsq1 <= lsq) & (check_reject(popt1) is None):
                popt, lsq = popt1, lsq1
                color = 'red'
                fitfunc = self.twogauss_func
        if lsq2 is not None:
            if (lsq2 <= lsq) & (check_reject(popt2) is None):
                popt, lsq = popt2, lsq2
                color = 'blue'
                fitfunc = self.twogauss_func
        if lsq3 is not None:
            if (lsq3 <= lsq):
                color = 'green'
                popt, lsq = popt3, lsq3
                popt[4] = 0    # Set to zero since unused
                fitfunc = self.onegauss_func

        if (popt is not None) & (window is not None):
            self.dfplot(self.x, self.y, popt, lsq, fitfunc, window=window,
                        color=color)

        return popt    # guess will handle None values


class dfunc_movie():
    def __init__(self, tt):
        self.tt = tt

    def subplot(self, volt, curr, xx, yy, ygrad, amp, window=plt, xlabel=None,
                labels=None, factor=None):
        if factor is None:
            factor = 1e6/9.08e3
        # Find peaks of yy
        peaks, _ = scipy.signal.find_peaks(
                       ygrad*amp, height=0.006*factor*amp, distance=20, 
                       prominence=0.003*factor*amp)
        if labels is None:
            window.plot(volt[xx], ygrad*amp, color='#0eaa57')
            window.plot(volt[xx], yy, color='#0e10e6')
            window.plot(volt, curr[self.tt, :], 'grey', alpha=0.7,
                        color='#f78f2e')
        else:
            window.plot(volt[xx], ygrad*amp, label='$-dI/dV$ * {0}'.\
                        format(amp), color='#0eaa57')
            window.plot(volt[xx], yy, label='current (Savitzky-Golay)',
                        color='#0e10e6')
            window.plot(volt, curr[self.tt, :], alpha=0.7,
                        label='current (original)', color='#f78f2e')
        window.plot(volt[xx[peaks]], ygrad[peaks]*amp, 'x')
        if window is not plt:
            if xlabel is not None:
                window.set_xlabel('Potential [V]', fontsize=30)
            window.set_ylabel('magnitude', fontsize=30)
            window.set_ylim([curr.min()*1.1, curr.max()*1.1])
        else:
            if xlabel is not None:
                window.xlabel('Potential [V]', fontsize=30)
            window.ylabel('magnitude', fontsize=30)
            window.ylim([curr.min()*1.1, curr.max()*1.1])
        window.tick_params(labelsize=20)
        window.legend(fontsize=16, loc='upper left')


''' --------------------------------------------------------------------------
    JOINT DISTRIBUTION FUNCTION ANALYSIS
------------------------------------------------------------------------------
'''
class join_dfunc():
    def __init__(self, time, voltL, voltR, currL, currR, trange=None,
                 dV=1, nstep=500, xrange=None, yrange=None, fid='fid'):
        # Store inputs
        self.time = time
        self.voltL = voltL
        self.voltR = voltR
        self.volt = np.concatenate([np.flip(-voltL), voltR])
        self.currL = currL
        self.currR = currR
        self.dV = dV
        self.fid = fid

        # Define the shape of currL/currR
        tL, nstepL = currL.shape
        tR, nstepR = currR.shape

        # Expecting trange to be an array [t1, t2] for range of movie
        if trange is None:
            nt = len(time)
            self.t1 = 0
            self.t2 = nt
        else:
            nt = trange[1] - trange[0]
            self.t1 = trange[0]
            self.t2 = trange[1]

        self.nstep = nstep
        self.nframes = nt // nstep

        # Plotting parameters:
        if yrange is None:
            self.yrange = [-0.0035, 0.035]
        else:
            self.yrange = yrange

        if xrange is None:
            self.xrange = [-40, 40]
        else:
            self.xrange = xrange

        # Define parameters to be used later
        self.arrTT = None
        self.arrTi = None
        self.enflag = None


    # Function to join the two distribution functions
    @staticmethod
    def set_dfunc(voltL, voltR, dataL, dataR, dV=1, nwindow=41, nwindowR=None,
                  order=3):
        # Create distribution function using two data arrays.
        # Find max, cut the curve, do it for the other side, then join them
        # at the top. Normalize to the mag of one side. Inputs are IV traces.

        if nwindowR is None:
            nwindowR = nwindow
        else:
            nwindowR = nwindowR

        # Calculate gradL/gradR. Note that the length of grad is reduced by
        # the window size and is even: int(nwindow/2)*2
        xL, yL, gradL = sgsmooth(dataL, nwindow=nwindow, repeat=order)
        xR, yR, gradR = sgsmooth(dataR, nwindow=nwindowR, repeat=order)

        vL = voltL[xL]
        vR = voltR[xR]

        dfuncL = dfunc(vL, gradL)
        dfuncR = dfunc(vR, gradR)

        # tbx.prefig()
        poptL = dfuncL.bestfit(window=None)
        poptR = dfuncR.bestfit(window=None)
        # plt.legend(fontsize=20, loc='upper left')

        # Choose leftmost peak of the bimodal distribution
        def check_popt(popt, grad, vLR):
            arg = np.argmax(grad)    # Default one-gauss peak value

            # Change this value if two-gauss is used
            if popt is not None:
                if popt[4] in [0, None]:
                    pp = popt[0]
                else:
                    pp = np.min([popt[0], popt[3]])
                arg_test = np.argmin(abs(vLR-pp))

                # Probably won't make sense if arg_test is too far from arg
                if abs(arg_test-arg)/(arg) < 0.10:
                    arg = arg_test
                
                print(np.argmin(abs(vLR-popt[0])), np.argmin(abs(vLR-popt[3])),
                      np.argmax(grad), arg)
            else:
                print(arg)
            return arg

        argL = check_popt(poptL, gradL, vL)
        argR = check_popt(poptR, gradR, vR)

        # Slice curves and only keep the right side
        sliceL = np.array(gradL[argL:])
        sliceR = np.array(gradR[argR:])

        # Normalize wrt right side of the curve
        factor = gradR[argR] / gradL[argL]
        index = np.arange(-len(sliceL), len(sliceR))
        dfLR = np.concatenate([np.flip(sliceL)*factor, sliceR])

        # Slice voltL/voltR as well, but also shift the starting value to zero
        if (voltL is not None) and (voltR is not None):
            vL = np.array(vL[argL:]) - vL[argL]
            vR = np.array(vR[argR:]) - vR[argR]
            vLvR = np.concatenate([np.flip(-vL), vR])
        else:
            vLvR = index*dV

        return index, dfLR, vLvR


    def calc_enint(self, dt=1):
        nsteps = int(len(self.currL[:, 0])/dt)
        arrTi = np.zeros(nsteps)
        for step in range(nsteps):
            tbx.progress_bar(step, nsteps)
            tt = dt * step
            # function can handle None
            _, dfunc, vLvR = self.set_dfunc(self.voltL, self.voltR,
                                            self.currL[tt, :], 
                                            self.currR[tt, :])
            arrTi[step] = enint(vLvR, dfunc)

        self.arrTT = self.time[[ii*dt+self.t1 for ii in range(nsteps)]]*1e3+5
        self.arrTi = arrTi
        self.enflag = 1


    # Plot Ti calculated from the energy integral
    def plot_enint(self):
        tbx.prefig(xlabel='time [ms]', ylabel='$T_i$ [eV]')
        plt.title('{0} $T_i$ from energy integral (combined distribution '
                  'function)'.format(self.fid), fontsize=20)
        plt.plot(self.arrTT, self.arrTi)
        tbx.savefig('./img/{0}-Ti-distfunc.png'.format(self.fid))


    def movie(self):
        # Plot movie to look at distribution function evolution
        if self.enflag is not None:
            fig = plt.figure(figsize=(16, 9))
            ax1 = fig.add_subplot(211)
            ax2 = fig.add_subplot(212)
        else:
            fig = plt.figure(figsize=(16, 4.5))
            ax2 = fig.add_subplot(111)

        def generate_frame(i):
            tt = i*self.nstep

            if self.enflag is not None:
                ax1.clear()
                ax1.set_title('{0} energy integral'.format(self.fid),
                              fontsize=25)
                ax1.plot(self.arrTT, self.arrTi)
                ax1.plot(np.repeat(trigtime(self.time, tt, off=self.t1), 2),
                         [np.amin(self.arrTi)*1.1, np.amax(self.arrTi)*1.1],
                         color='orange')
                ax1.set_xlabel('time [ms]', fontsize=30)
                ax1.set_ylabel('$T_i$ [eV]', fontsize=30)

            ax2.clear()
            ax2.set_title('Distribution function (positive towards old '
                          'LaB$_6$), t ={0:.3f} ms [{1}]'.format(trigtime(
                              self.time, tt, off=self.t1), tt), fontsize=20)
            _, dfunc, vLvR = self.set_dfunc(self.voltL, self.voltR,
                                            self.currL[tt, :],
                                            self.currR[tt, :])
            ax2.plot(vLvR, dfunc)
            ax2.set_xlabel('Potential [V]', fontsize=30)
            ax2.set_ylabel('f(V)', fontsize=30)
            ax2.tick_params(labelsize=20)
            ax2.set_ylim(self.yrange)
            ax2.set_xlim(self.xrange)

            plt.tight_layout()

            print('\r', 'Generating frame {0}/{1} ({2:.2f}%)...'
                  .format(i+1, self.nframes, (i+1)/self.nframes*100), end='')

        anim = animation.FuncAnimation(fig, generate_frame,
                                       frames=self.nframes, interval=25)
        anim.save('./videos/{0}-dfunc-combine.mp4'.format(self.fid))


''' --------------------------------------------------------------------------
    ENERGY INTEGRAL CALCULATION
------------------------------------------------------------------------------
'''
def enint(volt, dfunc):
    den = np.sum([jj/np.sqrt(abs(ii)) for ii, jj in zip(volt, dfunc)
                 if ii != 0])
    vavg = np.sum([jj*np.sqrt(abs(ii)) for ii, jj in zip(volt, dfunc)
                  if ii != 0])
    return vavg/den


''' ----------------------------------------------------------------------
    REGULAR DISTRIBUTION FUNCTION ROUTINES
--------------------------------------------------------------------------
'''
def get_dfunc(cacurr, snw=41, passes=3):
    # Gets the distribution function from a single I-V plot
    nt, nvolt = cacurr.shape
    nvolt -= 2*(snw//2)

    cacurr_sm = np.empty((nt,nvolt))
    grad_sm = np.empty((nt,nvolt))
    
    for tt in range(nt):
        tbx.progress_bar(tt, nt, label='tt')
        x, y, grad = sgsmooth(cacurr[tt,:], nwindow=snw, repeat=passes)
        cacurr_sm[tt,:] = y
        grad_sm[tt,:] = grad
    return x, cacurr_sm, grad_sm


def get_dfunc2(cacurrL, cacurrR, voltL, voltR, snw=41, order=3):
    # Joins two distribution functions from two different I-V plots
    nt, nvolt = cacurrL.shape
    dV = (voltL[-1]-voltL[0])/(nvolt-1)
    nv = nvolt//2
    vrange = np.arange(-nv, nv+1) * dV

    dfunc_arr = np.empty((nt,nvolt))

    for tt in range(0,nt):
        tbx.progress_bar(tt, nt, label='tt')
        index, func, revolt = dfunc(cacurrL[tt,:], cacurrR[tt,:],
                                    voltL=voltL, voltR=voltR)
        index += nv
        aaa = np.where((index>=0) & (index<vrange.shape))
        dfunc_arr[tt, index] = func

    return vrange, dfunc_arr


''' ----------------------------------------------------------------------
    REGULAR NON-CLASS FUNCTIONS
--------------------------------------------------------------------------
'''
def trigtime(time, ind, start=5, off=0):
    # Determines the actual time (in ms) from the start of the discharge
    # start: [ms] the start time of the trigger (recorded)
    # off:   [px] the offset of the analyzed slice of data
    return time[int(ind)+off]*1e3 + start


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
        tbx.prefig(xlabel='Discriminator grid voltage [V]',
                   ylabel='Current [$\mu$A]')
        plt.plot(volt, temp, color='#0e10e6')  # ,label='{0} ms'.format(mstime), 
        plt.title('exponential fit, t = {0:.2f} ms'.format(mstime),
                  fontsize=20)
        #plt.plot(volt[argmin:], temp[argmin:], color='#9208e7')
        plt.plot(volt, [vstart for ii in volt], '--', color='#5cd05b')
        plt.plot(volt, [vend for ii in volt], '--', color='#5cd05b')
        plt.plot(volt[argmin-20:], exp_func(volt[argmin-20:], *popt), '--',
                 label='$T_i$ = {0:.2f} eV'.format(Ti), color='#ff4900',
                 linewidth=2)
        plt.ylim(np.min(temp)*0.96, np.max(temp)*1.04)
        plt.legend(fontsize=20, loc='upper right')
        if save == 1:
            tbx.savefig('./img/{0}-IV-expfit-{1:.2f}ms.png'.format(
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
                                                            chan, trange[0],
                                                            trange[1]),
                  fontsize=20)

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


''' ----------------------------------------------------------------------
    DISTRIBUTION FUNCTIONS & SMOOTHING
--------------------------------------------------------------------------
'''
def rsmooth(data, repeat=2, nwindow=31, **kwargs):
    temp = data
    while repeat > 0:
        temp = tbx.smooth(temp, nwindow=nwindow, **kwargs)
        repeat -= 1
    return temp


def sgsmooth(data, nwindow=31, repeat=5):
    # Savitzky-Golay smoothing
    # returns (1) appropriately formatted x-values, (2) smoothed data,
    # (3) gradient
    xval = np.arange(len(data))
    x = xval[int(nwindow/2):-int(nwindow/2)]

    y = rsmooth(data, repeat=repeat, nwindow=nwindow)[int(
            nwindow/2):-int(nwindow/2)]

    grad_y = -np.gradient(y)

    return x, y, grad_y


def exv2(volt, data, **kwargs):
    # Expectation value of v^2 divide by density expectation value
    try:
        nwindow = kwargs.nwindow
    except:
        nwindow = 31    # default value of nwindow in rsmooth

    grad = -np.gradient(rsmooth(data, **kwargs))

    # Cut -dI/dV at the peak then symmetrize
    arg = np.argmax(grad[nwindow:-nwindow]) + nwindow
    cut = np.array(grad[arg:-nwindow])

    arrE = volt[arg:]-volt[arg]

    den = np.sum([jj/np.sqrt(ii) for ii, jj in zip(arrE, cut) if ii != 0])
    vavg = np.sum([jj*np.sqrt(ii) for ii, jj in zip(arrE, cut) if ii != 0])

    return vavg/den
