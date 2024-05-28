import logging
import os
import pprint
import sys
import time
import traceback
from pathlib import Path

import pcbnew
import wx

from .cfgman import ConfigMan
from .hdata import BaseSchData
from .interface import DlgHPCBRun
#from .placement import enforce_position

logger = logging.getLogger("hierpcb")
logger.setLevel(logging.DEBUG)


class HierarchicalPCBPlugin(pcbnew.ActionPlugin):
    def __init__(self):
        super().__init__()
        self.version = "0.0.1"

    def defaults(self):
        self.name = "HierarchicalPCB"
        self.category = "Layout"
        self.description = (
            "True hierarchical layouts to go with the hierarchical schematics."
            "You can define 'rooms' for different schematics throughout the hierarchy "
            "and this plugin will enforce them on the PCB."
        )
        self.icon_file_name = str(Path(__file__).parent / "icon.png")
        self.show_toolbar_button = True

    def Run(self):
        # grab PCB editor frame
        wx_frame = wx.FindWindowByName("PcbFrame")

        for lH in list(logger.handlers):
            logger.removeHandler(lH)
        logger.addHandler(
            logging.FileHandler(filename=pcbnew.GetBoard().GetFileName() + ".hierpcb.log", mode="w")
        )

        # set up logger
        logger.info(
            f"Plugin v{self.version} running on KiCad {pcbnew.GetBuildVersion()} and Python {sys.version} on {sys.platform}."
        )

        with ConfigMan(Path(pcbnew.GetBoard().GetFileName() + ".hierpcb.json")) as cfg:
            RunActual(cfg, wx_frame)


def RunActual(cfg, wx_frame: wx.Window):

    schData = BaseSchData(pcbnew.GetBoard())
    schData.load(cfg)

    if DlgHPCBRun(wx_frame, schData).ShowModal() == wx.ID_OK:
        schData.save(cfg)
        schData.replicate()
        logger.info("Saved.")
