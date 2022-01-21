"""
    Qudev specific driver for the HDAWG instrument.
"""

import logging

import pycqed.instrument_drivers.physical_instruments.ZurichInstruments.ZI_HDAWG_core as zicore
from pycqed.instrument_drivers.physical_instruments.ZurichInstruments import ZI_base_qudev

log = logging.getLogger(__name__)

class ZI_HDAWG_qudev(zicore.ZI_HDAWG_core,
                     ZI_base_qudev.ZI_base_instrument_qudev):
    """This is the Qudev specific PycQED driver for the HDAWG instrument
    from Zurich Instruments AG.
    """

    USER_REG_FIRST_SEGMENT = 5
    USER_REG_LAST_SEGMENT = 6

    def _check_options(self):
        """
        Override the method in ZI_HDAWG_core, to bypass the unneeded check for
        the PC option.
        """
        pass

    def clock_freq(self):
        return 2.4e9
