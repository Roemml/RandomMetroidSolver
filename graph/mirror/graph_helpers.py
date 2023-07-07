from graph.vanilla.graph_helpers import HelpersGraph
from logic.cache import Cache
from rom.rom_patches import RomPatches

# gates are reversed
class HelpersGraphMirror(HelpersGraph):
    def __init__(self, smbm):
        self.smbm = smbm

    @Cache.decorator
    def canPassRedTowerToMaridiaNode(self):
        sm = self.smbm
        return sm.wand(sm.haveItem('Morph'),
                       sm.wor(RomPatches.has(RomPatches.AreaRandoGatesBase),
                              sm.canGreenGateGlitch()))

    @Cache.decorator
    def canExitWaveBeam(self):
        sm = self.smbm
        return sm.wor(sm.haveItem('Morph'), # exit through lower passage under the spikes
                      sm.wand(sm.wor(sm.haveItem('SpaceJump'), # exit through blue gate
                                     sm.haveItem('Grapple')),
                              sm.haveItem('Wave')))

    # (this is actually Left to Right in mirror)
    def canPassFrogSpeedwayRightToLeft(self):
        return super().canPassFrogSpeedwayLeftToRight()

    # (this is actually Right to Left in mirror)
    def canPassFrogSpeedwayLeftToRight(self):
        return super().canPassFrogSpeedwayRightToLeft()
