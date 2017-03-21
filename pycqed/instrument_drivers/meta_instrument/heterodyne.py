# This is a virtual instrument abstracting a homodyne
# source which controls RF and LO sources

import logging
import numpy as np
from time import time
from qcodes.instrument.base import Instrument
from qcodes.utils import validators as vals
from qcodes.instrument.parameter import ManualParameter
# Used for uploading the right AWG sequences
from pycqed.measurement.pulse_sequences import standard_sequences as st_seqs
import time


class HeterodyneInstrument(Instrument):

    """
    This is a virtual instrument for a homodyne source

    Instrument is CBox, UHFQC and ATS compatible

    Todo:
        - Add power settings
        - Add integration time settings
        - Build test suite
        - Add parameter Heterodyne voltage (that returns a complex value / two
          values)
        - Add different demodulation settings.
        - Add fading plots that shows the last measured avg transients
          and points in the IQ-plane in the second window
        - Add option to use CBox integration averaging mode and verify
           identical results
    """
    shared_kwargs = ['RF', 'LO', 'AWG']

    def __init__(self, name, RF, LO, AWG=None, acquisition_instr=None,
                 acquisition_instr_controller=None,
                 single_sideband_demod=False, **kw):

        self.RF = RF

        self.common_init(name, LO, AWG, acquisition_instr,
                         single_sideband_demod, **kw)

        self.add_parameter('RF_power', label='RF power',
                           unit='dBm', vals=vals.Numbers(),
                           set_cmd=self._set_RF_power,
                           get_cmd=self._get_RF_power)
        self.add_parameter('acquisition_instr_controller',
                           set_cmd=self._set_acquisition_instr_controller,
                           get_cmd=self._get_acquisition_instr_controller,
                           vals=vals.Anything())
        self.acquisition_instr_controller(acquisition_instr_controller)
        self._RF_power = None

    def common_init(self, name, LO, AWG, acquisition_instr='CBox',
                    single_sideband_demod=False, **kw):
        logging.info(__name__ + ' : Initializing instrument')
        Instrument.__init__(self, name, **kw)

        self.LO = LO
        self.AWG = AWG
        self.add_parameter('frequency', label='Heterodyne frequency',
                           unit='Hz', vals=vals.Numbers(9e3, 40e9),
                           get_cmd=self._get_frequency,
                           set_cmd=self._set_frequency)
        self.add_parameter('f_RO_mod', label='Intermodulation frequency',
                           unit='Hz', vals=vals.Numbers(-600e6, 600e6),
                           set_cmd=self._set_f_RO_mod,
                           get_cmd=self._get_f_RO_mod)
        self.add_parameter('single_sideband_demod', vals=vals.Bool(),
                           label='Single sideband demodulation',
                           parameter_class=ManualParameter,
                           initial_value=single_sideband_demod)
        self.add_parameter('acquisition_instr', vals=vals.Strings(),
                           label='Acquisition instrument',
                           set_cmd=self._set_acquisition_instr,
                           get_cmd=self._get_acquisition_instr)
        self.add_parameter('nr_averages', label='Number of averages',
                           vals=vals.Numbers(min_value=0, max_value=1e6),
                           parameter_class=ManualParameter,
                           initial_value=1024)
        self.add_parameter('status', vals=vals.Enum('On','Off'),
                           set_cmd=self._set_status,
                           get_cmd=self._get_status)
        self.add_parameter('trigger_separation', label='Trigger separation',
                           unit='s', vals=vals.Numbers(0),
                           set_cmd=self._set_trigger_separation,
                           get_cmd=self._get_trigger_separation)
        self.add_parameter('RO_length', label='Readout length',
                           unit='s', vals=vals.Numbers(0),
                           set_cmd=self._set_RO_length,
                           get_cmd=self._get_RO_length)
        self.add_parameter('auto_seq_loading', vals=vals.Bool(),
                           label='Automatic AWG sequence loading',
                           parameter_class=ManualParameter,
                           initial_value=True)
        self.add_parameter('acq_marker_channels', vals=vals.Strings(),
                           label='Acquisition trigger channels',
                           set_cmd=self._set_acq_marker_channels,
                           get_cmd=self._get_acq_marker_channels)

        self._trigger_separation = 10e-6
        self._RO_length = 2274e-9
        self._awg_seq_filename = ''
        self._awg_seq_parameters_changed = True
        self._UHFQC_awg_parameters_changed = True
        self.acquisition_instr(acquisition_instr)
        self.status('Off')
        self._f_RO_mod = 10e6
        self._frequency = 5e9
        self.frequency(5e9)
        self.f_RO_mod(10e6)
        self._eps = 0.01 # Hz slack for comparing frequencies
        self._acq_marker_channels = 'ch4_marker1,ch4_marker2,' \
                                    'ch3_marker1,ch3_marker2'


    def prepare(self, get_t_base=True):
        if 'CBox' in self.acquisition_instr():
            self.prepare_CBox(get_t_base)
        elif 'UHFQC' in self.acquisition_instr():
            self.prepare_UHFQC()
        elif 'ATS' in self.acquisition_instr():
            self.prepare_ATS(get_t_base)
        else:
            raise ValueError("Invalid acquisition instrument {} in {}".format(
                self.acquisition_instr(), self.__class__.__name__))

        # turn on the AWG and the MWGs
        self.AWG.run()
        self.on()

    def prepare_CBox(self, get_t_base=True):
        # only uploads a seq to AWG if something changed
        if self.AWG != None:
            if (self._awg_seq_filename not in self.AWG.setup_filename() or
                    self._awg_seq_parameters_changed) and \
                    self.auto_seq_loading():
                self._awg_seq_filename = \
                    st_seqs.generate_and_upload_marker_sequence(
                        self.RO_length(), self.trigger_separation(),
                        RF_mod=False,
                        acq_marker_channels=self.acq_marker_channels())
                self._awg_seq_parameters_changed = False

        print('RO_length heterodyne', self.RO_length())
        if get_t_base:
            trace_length = 512
            tbase = np.arange(0, 5*trace_length, 5)*1e-9
            self.cosI = np.floor(
                127.*np.cos(2*np.pi*self.f_RO_mod()*tbase))
            self.sinI = np.floor(
                127.*np.sin(2*np.pi*self.f_RO_mod()*tbase))
            self._acquisition_instr.sig0_integration_weights(self.cosI)
            self._acquisition_instr.sig1_integration_weights(self.sinI)
            # because using integrated avg
            self._acquisition_instr.set('nr_samples', 1)
            self._acquisition_instr.nr_averages(int(self.nr_averages()))
        # self.CBox.set('acquisition_mode', 'idle') # aded with xiang

    def prepare_UHFQC(self):
        if self.AWG != None:
            if (self._awg_seq_filename not in self.AWG.setup_filename() or
                    self._awg_seq_parameters_changed) and \
                    self.auto_seq_loading():
                self._awg_seq_filename = \
                    st_seqs.generate_and_upload_marker_sequence(
                        5e-9, self.trigger_separation(), RF_mod=False,
                        acq_marker_channels=self.acq_marker_channels())
                self._awg_seq_parameters_changed = False

        if self._UHFQC_awg_parameters_changed and self.auto_seq_loading():
            self._acquisition_instr.awg_sequence_acquisition()
            self._UHFQC_awg_parameters_changed = False

        # prepare weights and rotation
        if self.single_sideband_demod():
            self._acquisition_instr.prepare_SSB_weight_and_rotation(
                IF=self.f_RO_mod(), weight_function_I=0, weight_function_Q=1)
        else:
            self._acquisition_instr.prepare_DSB_weight_and_rotation(
                IF=self.f_RO_mod(), weight_function_I=0, weight_function_Q=1)

        # this sets the result to integration and rotation outcome
        self._acquisition_instr.quex_rl_source(2)
        self._acquisition_instr.quex_rl_length(1)
        self._acquisition_instr.quex_rl_avgcnt(
            int(np.log2(self.nr_averages())))
        self._acquisition_instr.quex_wint_length(int(self.RO_length()*1.8e9))
        # The AWG program uses userregs/0 to define the number of
        # iterations in the loop
        self._acquisition_instr.awgs_0_userregs_0(int(self.nr_averages()))
        # 0 for rl, 1 for iavg
        self._acquisition_instr.awgs_0_userregs_1(0)
        self._acquisition_instr.awgs_0_single(1)

    def prepare_ATS(self, get_t_base=True):
        if self.AWG != None:
            if (self._awg_seq_filename not in self.AWG.setup_filename() or
                    self._awg_seq_parameters_changed) and \
                    self.auto_seq_loading():
                self._awg_seq_filename = \
                    st_seqs.generate_and_upload_marker_sequence(
                        self.RO_length(), self.trigger_separation(),
                        RF_mod=False,
                        acq_marker_channels=self.acq_marker_channels())
                self._awg_seq_parameters_changed = False

        if get_t_base:
            self._acquisition_instr_controller.demodulation_frequency = \
                self.f_RO_mod()
            buffers_per_acquisition = 8
            self._acquisition_instr_controller.update_acquisitionkwargs(
                #mode='NPT',
                samples_per_record=64*1000,#4992,
                records_per_buffer=
                    int(self.nr_averages()/buffers_per_acquisition),#70 segments
                buffers_per_acquisition=buffers_per_acquisition,
                channel_selection='AB',
                transfer_offset=0,
                external_startcapture='ENABLED',
                enable_record_headers='DISABLED',
                alloc_buffers='DISABLED',
                fifo_only_streaming='DISABLED',
                interleave_samples='DISABLED',
                get_processed_data='DISABLED',
                allocated_buffers=buffers_per_acquisition,
                buffer_timeout=1000)

    def probe(self):
        if 'CBox' in self.acquisition_instr():
            return self.probe_CBox()
        elif 'UHFQC' in self.acquisition_instr():
            return self.probe_UHFQC()
        elif 'ATS' in self.acquisition_instr():
            return self.probe_ATS()
        else:
            raise ValueError("Invalid acquisition instrument {} in {}".format(
                self.acquisition_instr(), self.__class__.__name__))

    def probe_CBox(self):
        if self.single_sideband_demod():
            demodulation_mode = 'single'
        else:
            demodulation_mode = 'double'
        self._acquisition_instr.acquisition_mode('idle')
        self._acquisition_instr.acquisition_mode('integration averaging')
        self._acquisition_instr.demodulation_mode(demodulation_mode)
        # d = self.CBox.get_integrated_avg_results()
        # quick fix for spec units. Need to properrly implement it later
        # after this, output is in mV
        scale_factor_dacmV = 1000.*0.75/128.
        # scale_factor_integration = 1./float(self.f_RO_mod() *
        #     self.CBox.nr_samples()*5e-9)
        scale_factor_integration = \
            1 / (64.*self._acquisition_instr.integration_length())
        factor = scale_factor_dacmV*scale_factor_integration
        d = np.double(self._acquisition_instr.get_integrated_avg_results()) \
            * np.double(factor)
        # print(np.size(d))
        return d[0][0]+1j*d[1][0]

    def probe_UHFQC(self):
        if self._awg_seq_parameters_changed or \
           self._UHFQC_awg_parameters_changed:
            self.prepare()

        self._acquisition_instr.awgs_0_enable(1)

        # why do we need this?
        try:
            self._acquisition_instr.awgs_0_enable()
        except:
            self._acquisition_instr.awgs_0_enable()

        while self._acquisition_instr.awgs_0_enable() == 1:
            time.sleep(0.01)
        data = ['', '']
        data[0] = self._acquisition_instr.quex_rl_data_0()[0]['vector']
        data[1] = self._acquisition_instr.quex_rl_data_1()[0]['vector']
        return data[0]+1j*data[1]

    def probe_ATS(self):
        t0 = time.time()
        dat = self._acquisition_instr_controller.acquisition()
        t1 = time.time()
        # print("time for ATS polling", t1-t0)
        return dat

    def finish(self):
        self.off()
        self.AWG.stop()

    def _set_frequency(self, val):
        self._frequency = val
        # this is the definition agreed upon in issue 131
        self.RF.frequency(val)
        self.LO.frequency(val-self._f_RO_mod)

    def _get_frequency(self):
        freq = self.RF.frequency()
        LO_freq = self.LO.frequency()
        if abs(LO_freq - freq + self._f_RO_mod) > self._eps:
            logging.warning('f_RO_mod between RF and LO is not set correctly')
            logging.warning('\tf_RO_mod = {}, LO_freq = {}, RF_freq = {}'
                            .format(self._f_RO_mod, LO_freq, freq))
        if abs(self._frequency - freq) > self._eps:
            logging.warning('Heterodyne frequency does not match RF frequency')
        return self._frequency

    def _set_f_RO_mod(self, val):
        self._f_RO_mod = val
        self.LO.frequency(self._frequency - val)

    def _get_f_RO_mod(self):
        freq = self.RF.frequency()
        LO_freq = self.LO.frequency()
        if abs(LO_freq - freq + self._f_RO_mod) > self._eps:
            logging.warning('f_RO_mod between RF and LO is not set correctly')
            logging.warning('\tf_RO_mod = {}, LO_freq = {}, RF_freq = {}'
                            .format(self._f_RO_mod, LO_freq, freq))
        return self._f_RO_mod

    def _set_RF_power(self, val):
        self.RF.power(val)
        self._RF_power = val
        # internally stored to allow setting RF from stored setting

    def _get_RF_power(self):
        return self._RF_power

    def _set_status(self, val):
        if val == 'On':
            self.on()
        else:
            self.off()

    def _get_status(self):
        if (self.LO.status().startswith('On') and
            self.RF.status().startswith('On')):
            return 'On'
        elif (self.LO.status().startswith('Off') and
              self.RF.status().startswith('Off')):
            return 'Off'
        else:
            return 'LO: {}, RF: {}'.format(self.LO.status(), self.RF.status())

    def on(self):
        if self.LO.status().startswith('Off') or \
           self.RF.status().startswith('Off'):
            wait = True
        else:
            wait = False
        self.LO.on()
        self.RF.on()
        if wait:
            # The R&S MWG-s take some time to stabilize their outputs
            time.sleep(1.0)

    def off(self):
        self.set('status', 'Off')
        return

    def prepare(self,  trigger_separation, RO_length, get_t_base=True ):
        '''
        This function needs to be overwritten for the ATS based version of this
        driver
        '''
        if self.AWG!=None:
            if ((self._awg_seq_filename not in self.AWG.get('setup_filename')) and
                    not self._disable_auto_seq_loading):
                self.seq_name = st_seqs.generate_and_upload_marker_sequence(
                    RO_length, trigger_separation, RF_mod=False,
                    IF=self.get('f_RO_mod'), mod_amp=0.5)
            self.AWG.run()
        if get_t_base is True:
            if self.acquisition_instr()==None:
                print('no acquistion prepare')
            elif 'CBox' in self.acquisition_instr():
                trace_length = 512
                tbase = np.arange(0, 5*trace_length, 5)*1e-9
                self.cosI = np.floor(
                    127.*np.cos(2*np.pi*self.get('f_RO_mod')*tbase))
                self.sinI = np.floor(
                    127.*np.sin(2*np.pi*self.get('f_RO_mod')*tbase))
                self._acquisition_instr.sig0_integration_weights(self.cosI)
                self._acquisition_instr.sig1_integration_weights(self.sinI)
                # because using integrated avg
                self._acquisition_instr.set('nr_samples', 1)
                self._acquisition_instr.nr_averages(int(self.nr_averages()))

            elif 'UHFQC' in self.acquisition_instr():
                # self._acquisition_instr.prepare_DSB_weight_and_rotation(
                #     IF=self.get('f_RO_mod'),
                #      weight_function_I=0, weight_function_Q=1)
                # this sets the result to integration and rotation outcome
                self._acquisition_instr.quex_rl_source(2)
                # only one sample to average over
                self._acquisition_instr.quex_rl_length(1)
                self._acquisition_instr.quex_rl_avgcnt(
                    int(np.log2(self.nr_averages())))
                self._acquisition_instr.quex_wint_length(
                    int(RO_length*1.8e9))
                # Configure the result logger to not do any averaging
                # The AWG program uses userregs/0 to define the number o
                # iterations in the loop
                self._acquisition_instr.awgs_0_userregs_0(
                    int(self.nr_averages()))
                # 0 for rl, 1 for iavg
                self._acquisition_instr.awgs_0_userregs_1(0)
                self._acquisition_instr.awgs_0_single(1)
                self._acquisition_instr.acquisition_initialize([0,1], 'rl')
                self.scale_factor = 1/(1.8e9*RO_length*int(self.nr_averages()))


            elif 'ATS' in self.acquisition_instr():
                self._acquisition_instr_controller.demodulation_frequency=self.get('f_RO_mod')
                buffers_per_acquisition = 8
                self._acquisition_instr_controller.update_acquisitionkwargs(#mode='NPT',
                     samples_per_record=64*1000,#4992,
                     records_per_buffer=int(self.nr_averages()/buffers_per_acquisition),#70, segmments
                     buffers_per_acquisition=buffers_per_acquisition,
                     channel_selection='AB',
                     transfer_offset=0,
                     external_startcapture='ENABLED',
                     enable_record_headers='DISABLED',
                     alloc_buffers='DISABLED',
                     fifo_only_streaming='DISABLED',
                     interleave_samples='DISABLED',
                     get_processed_data='DISABLED',
                     allocated_buffers=buffers_per_acquisition,
                     buffer_timeout=1000)

    def _set_acquisition_instr(self, acquisition_instr):
        # Specifying the int_avg det here should allow replacing it with ATS
        # or potential digitizer acquisition easily
        if acquisition_instr==None:
            self._acquisition_instr=None
        else:
            self._acquisition_instr = self.find_instrument(acquisition_instr)
        self._awg_seq_parameters_changed = True
        self._UHFQC_awg_parameters_changed = True

    def _get_acquisition_instr_controller(self):
        # Specifying the int_avg det here should allow replacing it with ATS
        # or potential digitizer acquisition easily
        if self._acquisition_instr_controller == None:
            return None
        else:
            return self._acquisition_instr_controller.name

    def _set_acquisition_instr_controller(self, acquisition_instr_controller):
        # Specifying the int_avg det here should allow replacing it with ATS
        # or potential digitizer acquisition easily
        if acquisition_instr_controller == None:
            self._acquisition_instr_controller = None
        else:
            self._acquisition_instr_controller = \
                self.find_instrument(acquisition_instr_controller)
            print("controller initialized")


    def probe(self, demodulation_mode='double', **kw):
        '''
        Starts acquisition and returns the data
            'COMP' : returns data as a complex point in the I-Q plane in Volts
        '''
        if self.acquisition_instr()==None:
            dat=[0,0]
            print('no acquistion probe')
        elif 'CBox' in self.acquisition_instr():
            self._acquisition_instr.set('acquisition_mode', 'idle')
            self._acquisition_instr.set(
                'acquisition_mode', 'integration averaging')
            self._acquisition_instr.demodulation_mode(demodulation_mode)
            # d = self.CBox.get_integrated_avg_results()
            # quick fix for spec units. Need to properrly implement it later
            # after this, output is in mV
            scale_factor_dacmV = 1000.*0.75/128.
            # scale_factor_integration = 1./float(self.f_RO_mod()*self.CBox.nr_samples()*5e-9)
            scale_factor_integration = 1. / \
                (64.*self._acquisition_instr.integration_length())
            factor = scale_factor_dacmV*scale_factor_integration
            d = np.double(
                self._acquisition_instr.get_integrated_avg_results())*np.double(factor)
            # print(np.size(d))
            dat = (d[0][0]+1j*d[1][0])
        elif 'UHFQC' in self.acquisition_instr():
            t0 = time.time()
            #self._acquisition_instr.awgs_0_enable(1) #this was causing spikes
            # NH: Reduced timeout to prevent hangups


            dataset = self._acquisition_instr.acquisition_poll(samples=1, acquisition_time=0.001, timeout=10)
            dat = (self.scale_factor*dataset[0][0]+self.scale_factor*1j*dataset[1][0])
            t1 = time.time()
            # print("time for UHFQC polling", t1-t0)
        elif 'ATS' in self.acquisition_instr():
            t0 = time.time()
            dat = self._acquisition_instr_controller.acquisition()
            t1 = time.time()
            # print("time for ATS polling", t1-t0)
        return dat

    def finish(self):
        if 'UHFQC' in self.acquisition_instr():
            self._acquisition_instr.acquisition_finalize()


    def get_demod_array(self):
        return self.cosI, self.sinI

    def demodulate_data(self, dat):
        """
        Returns a complex point in the IQ plane by integrating and demodulating
        the data. Demodulation is done based on the 'f_RO_mod' and
        'single_sideband_demod' parameters of the Homodyne instrument.
        """
        if self._f_RO_mod != 0:
            # self.cosI is based on the time_base and created in self.init()
            if self._single_sideband_demod is True:
                # this definition for demodulation is consistent with
                # issue #131
                I = np.average(self.cosI * dat[0] + self.sinI * dat[1])
                Q = np.average(-self.sinI * dat[0] + self.cosI * dat[1])
            else:  # Single channel demodulation, defaults to using channel 1
                I = 2*np.average(dat[0]*self.cosI)
                Q = 2*np.average(dat[0]*self.sinI)
        else:
            I = np.average(dat[0])
            Q = np.average(dat[1])
        return I+1.j*Q


class LO_modulated_Heterodyne(HeterodyneInstrument):

    """
    Homodyne instrument for pulse modulated LO.
    Inherits functionality from the HeterodyneInstrument

    AWG is used for modulating signal and triggering the CBox for acquisition
    or AWG is used for triggering and UHFQC for modulation and acquisition
    """
    shared_kwargs = ['RF', 'LO', 'AWG']

    def __init__(self, name, LO, AWG, acquisition_instr='CBox',
                 single_sideband_demod=False, **kw):

        self.common_init(name, LO, AWG, acquisition_instr,
                         single_sideband_demod, **kw)

        self.add_parameter('mod_amp', label='Modulation amplitude',
                           unit='V', vals=vals.Numbers(0, 1),
                           set_cmd=self._set_mod_amp,
                           get_cmd=self._get_mod_amp)
        self.add_parameter('acquisition_delay', label='Acquisition delay',
                           unit='s', vals=vals.Numbers(0, 1e-3),
                           set_cmd=self._set_acquisition_delay,
                           get_cmd=self._get_acquisition_delay)
        self.add_parameter('I_channel', vals=vals.Strings(),
                           label='I channel',
                           set_cmd=self._set_I_channel,
                           get_cmd=self._get_I_channel)
        self.add_parameter('Q_channel', vals=vals.Strings(),
                           label='Q channel',
                           set_cmd=self._set_Q_channel,
                           get_cmd=self._get_Q_channel)

        self._f_RO_mod = 10e6
        self._frequency = 5e9
        self.f_RO_mod(10e6)
        self.frequency(5e9)
        self._mod_amp = 0
        self.mod_amp(.5)
        self._acquisition_delay = 0
        self.acquisition_delay(200e-9)
        self._I_channel = 'ch3'
        self._Q_channel = 'ch4'

    def prepare_CBox(self, get_t_base=True):
        """
        uses the AWG to generate the modulating signal and CBox for readout
        """
        # only uploads a seq to AWG if something changed
        if (self._awg_seq_filename not in self.AWG.setup_filename() or
                self._awg_seq_parameters_changed) and self.auto_seq_loading():
            self._awg_seq_filename = \
                st_seqs.generate_and_upload_marker_sequence(
                    self.RO_length(), self.trigger_separation(), RF_mod=True,
                    IF=self.f_RO_mod(), mod_amp=0.5,
                    acq_marker_channels=self.acq_marker_channels(),
                    I_channel=self.I_channel(), Q_channel=self.Q_channel())
            self.AWG.ch3_amp(self.mod_amp())
            self.AWG.ch4_amp(self.mod_amp())
            self._awg_seq_parameters_changed = False

        self.AWG.ch3_amp(self.mod_amp())
        self.AWG.ch4_amp(self.mod_amp())


        if get_t_base is True:
            trace_length = self.CBox.nr_samples()
            tbase = np.arange(0, 5*trace_length, 5)*1e-9
            self.cosI = np.cos(2*np.pi*self.f_RO_mod()*tbase)
            self.sinI = np.sin(2*np.pi*self.f_RO_mod()*tbase)

        self.CBox.nr_samples(1)  # because using integrated avg

    def prepare_UHFQC(self):
        """
        uses the UHFQC to generate the modulating signal and readout
        """
        # only uploads a seq to AWG if something changed
        if (self._awg_seq_filename not in self.AWG.setup_filename() or
                self._awg_seq_parameters_changed) and self.auto_seq_loading():
            self._awg_seq_filename = \
                st_seqs.generate_and_upload_marker_sequence(
                    5e-9, self.trigger_separation(), RF_mod=False,
                    acq_marker_channels=self.acq_marker_channels())
            self._awg_seq_parameters_changed = False

        # reupload the UHFQC pulse generation only if something changed
        if self._UHFQC_awg_parameters_changed and self.auto_seq_loading():
            self._acquisition_instr.awg_sequence_acquisition_and_pulse_SSB(
                self.f_RO_mod.get(), self.mod_amp(), RO_pulse_length=800e-9,
                acquisition_delay=self.acquisition_delay())
            self._UHFQC_awg_parameters_changed = False

        # prepare weights and rotation
        if self.single_sideband_demod():
            self._acquisition_instr.prepare_SSB_weight_and_rotation(
                IF=self.f_RO_mod(), weight_function_I=0, weight_function_Q=1)
        else:
            self._acquisition_instr.prepare_DSB_weight_and_rotation(
                IF=self.f_RO_mod(), weight_function_I=0, weight_function_Q=1)

        # this sets the result to integration and rotation outcome
        self._acquisition_instr.quex_rl_source(2)
        self._acquisition_instr.quex_rl_length(1)
        self._acquisition_instr.quex_rl_avgcnt(
            int(np.log2(self.nr_averages())))
        self._acquisition_instr.quex_wint_length(int(self.RO_length()*1.8e9))
        # The AWG program uses userregs/0 to define the number of
        # iterations in the loop
        self._acquisition_instr.awgs_0_userregs_0(int(self.nr_averages()))
        self._acquisition_instr.awgs_0_userregs_1(0) # 0 for rl, 1 for iavg
        self._acquisition_instr.awgs_0_userregs_2(
            int(self.acquisition_delay()*1.8e9/8))
        self._acquisition_instr.awgs_0_single(1)

    def probe_CBox(self):
        if self._awg_seq_parameters_changed:
            self.prepare()
        self.CBox.acquisition_mode(0)
        self.CBox.acquisition_mode(4)
        d = self.CBox.get_integrated_avg_results()
        return d[0][0]+1j*d[1][0]

    def _set_frequency(self, val):
        self._frequency = val
        # this is the definition agreed upon in issue 131
        # AWG modulation ensures that signal ends up at RF-frequency
        self.LO.frequency(val-self._f_RO_mod)

    def _get_frequency(self):
        freq = self.LO.frequency() + self._f_RO_mod
        if abs(self._frequency - freq) > self._eps:
            logging.warning('Homodyne frequency does not match LO frequency'
                            ' + RO_mod frequency')
        return self._frequency

    def _set_f_RO_mod(self, val):
        if val != self._f_RO_mod:
            if 'CBox' in self.acquisition_instr():
                self._awg_seq_parameters_changed = True
            elif 'UHFQC' in self.acquisition_instr():
                self._UHFQC_awg_parameters_changed = True
            self.frequency(self._frequency)
        self._f_RO_mod = val

    def _get_f_RO_mod(self):
        return self._f_RO_mod

    def _set_mod_amp(self, val):
        if val != self._mod_amp:
            if 'UHFQC' in self.acquisition_instr():
                self._UHFQC_awg_parameters_changed = True
        self._mod_amp = val

    def _get_mod_amp(self):
        return self._mod_amp


    def _set_acquisition_delay(self, val):
        if 'UHFQC' in self.acquisition_instr():
            self._acquisition_instr.awgs_0_userregs_2(int(val*1.8e9/8))
        else:
            raise NotImplementedError("CBox heterodyne driver does not "
                                      "implement acquisition delay")
        self._acquisition_delay = val

    def _get_acquisition_delay(self):
        return self._acquisition_delay

    def on(self):
        if self.LO.status().startswith('Off'):
            self.LO.on()
            time.sleep(1.0)

    def off(self):
        self.LO.off()

    def _get_status(self):
        return self.LO.get('status')

    def _set_I_channel(self, channel):
        if channel != self._I_channel and \
                not 'UHFQC' in self.acquisition_instr():
            self._awg_seq_parameters_changed = True
        self._I_channel = channel

    def _get_I_channel(self):
        return self._I_channel

    def _set_Q_channel(self, channel):
        if channel != self._Q_channel and \
                not 'UHFQC' in self.acquisition_instr():
            self._awg_seq_parameters_changed = True
        self._Q_channel = channel

    def _get_Q_channel(self):
        return self._Q_channel
