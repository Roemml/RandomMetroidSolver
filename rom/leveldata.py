# from SM3E

import sys
from enum import Enum, IntFlag, auto, IntEnum
import logging
from collections import defaultdict
import utils.log
from rom.rom import snes_to_pc, pc_to_snes
from rom.compression import Compressor
from utils.doorsmanager import plmRed, plmGreen, plmYellow, plmGrey, Facing, plmFacing, indicatorsDirection
from rom.romreader import RomReader

doorPlms = plmRed + plmGreen + plmYellow + plmGrey

logger = utils.log.get('LevelData')

class Ship(object):
    # plm sleep instruction
    sleepInstr = 0x812F

    def __init__(self, name, rom, enemy, center):
        self.log = logger
        self.name = name
        self.rom = rom
        self.enemy = enemy
        self.center = center
        self.bank = self.enemy.bank << 16

        self.load()

    def getShipAddr(self, addr):
        return self.bank + addr

    def findInstrListAddrs(self):
        # opcodes
        sta = 0x9D
        lda = 0xA9
        rtl = 0x6B
        rts = 0x60

        self.rom.seek(snes_to_pc(self.getShipAddr(self.enemy.initAi)))
        code = []
        for i in range(256):
            byte = self.rom.readByte()
            code.append(byte)
            if byte in (rtl, rts):
                break

        # we're looking for that:
        # $A2:A659 A9 16 A6    LDA #$A616             ;\
        # $A2:A65C 9D 92 0F    STA $0F92,x[$7E:0F92]  ;} Enemy instruction list pointer = $A616
        # $A2:A6F4 A9 1C A6    LDA #$A61C             ;\ Else ([enemy parameter 2] = 0):
        # $A2:A6F7 9D 92 0F    STA $0F92,x[$7E:0FD2]  ;} Enemy instruction list pointer = $A61C
        instrListAddrs = []
        for i, opcode in enumerate(code):
            if opcode != lda:
                continue
            if i+4 > len(code):
                break
            if code[i+3] != sta:
                continue
            if code[i+4] != 0x92 or code[i+5] != 0x0F:
                continue
            # we found it
            instrListAddrs.append(code[i+1] + (code[i+2] << 8))
            self.log.debug("instr list addr found: {} ({} {})".format(hex(instrListAddrs[-1]), hex(code[i+1]), hex(code[i+2])))

        if not instrListAddrs:
            raise Exception("at {} ship has custom ASM for its init".format(hex(self.enemy.initAi)))

        return instrListAddrs

    def load(self):
        instrListAddrs = self.findInstrListAddrs()

        self.spritemapAddr = None
        for addr in instrListAddrs:
            # load instruction list
            self.rom.seek(snes_to_pc(self.getShipAddr(addr)))

            # first word is frame delay
            frameDelay = self.rom.readWord()
            if frameDelay >= 0x8000:
                self.log.debug("at {} not a frame delay: {}".format(hex(addr), hex(frameDelay)))
                continue

            # second word is spritemap pointer
            spritemapAddr = self.rom.readWord()
            if spritemapAddr < 0x8000:
                self.log.debug("at {} not a spritemap pointer: {}".format(hex(addr+2), hex(spritemapAddr)))
                continue

            # third word is sleep
            sleep = self.rom.readWord()
            if sleep != Ship.sleepInstr:
                self.log.debug("at {} not a sleep instruction: {}".format(hex(addr+4), hex(sleep)))
                continue

            # found
            self.spritemapAddr = spritemapAddr
            break

        if not self.spritemapAddr:
            raise Exception("Can't find spritemap addr")

        # load spritemaps
        self.spritemap = Spritemap(self.rom, self.getShipAddr(self.spritemapAddr), self.center)

class Spritemap(object):
    def __init__(self, rom, dataAddr, center=None):
        self.log = logger
        self.rom = rom
        self.log.debug("load spritemap at {}".format(hex(dataAddr)))
        self.dataAddr = dataAddr
        self.center = center
        self.load()

    def load(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        self.oamCount = self.rom.readWord()
        if self.oamCount > 0x100:
            raise Exception("at {} spritemap oam count is too high: {}".format(hex(self.dataAddr), hex(self.oamCount)))
        self.log.debug("load spritemap at {} with {} OAMs".format(hex(self.dataAddr), self.oamCount))
        self.oams = []
        for i in range(self.oamCount):
            curAddr = 2+ self.dataAddr + i * OAM.size
            oam = OAM(self.rom, curAddr, self.center)
            #oam.debug()
            self.oams.append(oam)

        self.boundingRect = self.getBoundingRect()

    def write(self):
        self.log.debug("write spritemap ${:06x}".format(self.dataAddr))
        self.rom.seek(snes_to_pc(self.dataAddr))
        self.rom.writeWord(self.oamCount)
        for oam in self.oams:
            oam.write()

    def getBoundingRect(self):
        r = BoundingRect()
        for oam in self.oams:
            r.add(oam.realX, oam.realY)
        #r.debug()
        return r

    def transform(self, transformation):
        if transformation == Transform.Mirror:
            for oam in self.oams:
                oam.transform(transformation)

    def displayASM(self):
        out = ""
        out += "org ${:06x}\n".format(self.dataAddr)
        out += "    dw ${:04x} : ".format(self.oamCount)
        out += " : ".join([oam.displayASM() for oam in self.oams])
        out += "\n"
        return (self.dataAddr, out)

class Tilemap(object):
    def __init__(self, rom, dataAddr, size, rowSize):
        self.log = logger
        self.rom = rom
        self.log.debug("load tilemap at {}".format(hex(dataAddr)))
        self.dataAddr = dataAddr

        # (width, height) in bytes of the complete tilemap of the boss.
        # for example crocomire tilemap is 24 bytes x 14 bytes 
        self.size = size
        # in bytes, length of a hardware tilemap row (usually 0x40 bytes)
        self.rowSize = rowSize

        self.load()

    def load(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        fffe = self.rom.readWord()
        if fffe != 0xfffe:
            raise Exception("at {:x} tilemap doesn't start with FFFF".format(self.dataAddr))

        self.destAddr = []
        self.tileCount = []
        self.tiles = []

        cur = 0
        while True:
            destAddr = self.rom.readWord()
            if destAddr == 0xFFFF:
                break
            self.destAddr.append(destAddr)
            self.tileCount.append(self.rom.readWord())
            baseAddr = pc_to_snes(self.rom.tell())
            if self.tileCount[cur] > 0x100:
                raise Exception("at {} tilemap tile count is too high: {}".format(hex(self.dataAddr), hex(self.tileCount)))
            self.log.debug("load tilemap at {} with {} tiles".format(hex(baseAddr), self.tileCount[cur]))
            self.tiles.append([])
            for i in range(self.tileCount[cur]):
                tile = self.rom.readWord()
                self.tiles[cur].append(tile)
            cur += 1

    def write(self):
        self.log.debug("write tilemap ${:06x}".format(self.dataAddr))
        self.rom.seek(snes_to_pc(self.dataAddr))
        self.rom.writeWord(0xFFFE)
        for i in range(len(self.destAddr)):
            self.rom.writeWord(self.destAddr[i])
            self.rom.writeWord(self.tileCount[i])
            for tile in self.tiles[i]:
                self.rom.writeWord(tile)
        self.rom.writeWord(0xFFFF)

    def transform(self, transformation):
        if transformation == Transform.Mirror:
            newTiles = []
            newDestAddr = []

            for i, destAddr in enumerate(self.destAddr):
                # compute new start address
                baseAddr = destAddr & (((~self.rowSize)+1) & 0xffff)
                pos = destAddr % self.rowSize
                newAddr = baseAddr + self.size[0] - pos - self.tileCount[i]*2 # - 2 # a tile is two bytes
                newDestAddr.append(newAddr)

                newTiles.append([])
                for t in reversed(self.tiles[i]):
                    vflip = (t >> 14) & 1
                    vflip = 1 - vflip
                    t = (t & 0xbfff) | (vflip << 14)
                    newTiles[-1].append(t)

            self.destAddr = newDestAddr
            self.tiles = newTiles

    def displayASM(self):
        out = ""
        out += "org ${:06x}\n".format(self.dataAddr)
        out += "    dw $FFFE\n"
        for i in range(len(self.destAddr)):
            out += "    dw ${:04x},${:04x}, ".format(self.destAddr[i], self.tileCount[i])
            out += ",".join("${:04x}".format(t) for t in self.tiles[i])
            out += "\n"
        out += "    dw $FFFF\n"
        return (self.dataAddr, out)

class BoundingRect(object):
    def __init__(self, initial=None):
        self.log = logger
        if initial is None:
            # top left corner, y=0 is top, x=0 is left
            self.x1 = sys.maxsize
            self.y1 = sys.maxsize

            # bottom right corner
            self.x2 = 0
            self.y2 = 0
        else:
            self.x1, self.y1, self.x2, self.y2 = initial

        # 16x16 pixels
        self.size = 16

    def add(self, x, y):
        if x < self.x1:
            self.x1 = x
        if y < self.y1:
            self.y1 = y
        if x+self.size > self.x2:
            self.x2 = x+self.size
        if y+self.size > self.y2:
            self.y2 = y+self.size

    def isInside(self, x, y):
        # x,y are in 16x16 tile size
        x += 1
        x *= 16
        y *= 16
        return x > self.x1 and x < self.x2 and y > self.y1 and y < self.y2

    def debug(self):
        self.log.debug("bounding rect:")
        self.log.debug("{:3} {:3}           ".format(self.x1, self.y1))
        self.log.debug("           {:3} {:3}".format(self.x2, self.y2))

    def width(self):
        return (self.x2 - self.x1)//16

    def height(self):
        return (self.y2 - self.y1)//16

    def start(self):
        return (self.x1//16, self.y1//16)

class Int2(object):
    # 2 complement integer
    def __init__(self, encValue, mask):
        self.encValue = encValue
        self.mask = mask
        if self._isEncNeg(self.encValue):
            self.value = - self._getEncInv(self.encValue)
        else:
            self.value = self.encValue

    def _isEncNeg(self, encValue):
        return ((self.mask+1)>>1) & encValue != 0

    def _getEncInv(self, encValue):
        return ((~encValue)+1) & self.mask

    def _getEnc(self):
        if self.value >= 0:
            return self.value
        else:
            return self._getEncInv(-self.value)

    def _post(self):
        if self.value >= 0:
            self.value = self.value % (self.mask >> 1)
        else:
            self.value = self.value % -(self.mask >> 1)
        self.encValue = self._getEnc()

    def add(self, n):
        self.value += n
        self._post()

    def sub(self, n):
        self.value -= n
        self._post()

    def mul(self, n):
        self.value *= n
        self._post()

    def get(self):
        return self.encValue

class OAM(object):
    # an oam entry is made of five bytes: (s000000 x xxxxxxxx) (yyyyyyyy) (YXppPPPt tttttttt)
    #  s = size bit
    #      0: 8x8
    #      1: 16x16
    #  x = X offset of sprite from centre
    #  y = Y offset of sprite from centre
    #  Y = Y flip
    #  X = X flip
    #  p = priority (relative to background)
    #  t = tile number

    size = 5

    def __init__(self, rom, dataAddr, center=None):
        self.log = logger
        self.rom = rom
        self.dataAddr = dataAddr
        # (x, y) position in the displayed screen
        # $12: Y pos, $14 X pos as parameter to 818AB8
        self.center = center
        self.load()

    def fixX(self, lowerX, highX):
        if self.center is None:
            return 0

        if highX == 0:
            # after center
            return lowerX + self.center[0]
        else:
            # before center
            return (lowerX + self.center[0]) & 0xFF

    def fixY(self, y):
        if self.center is None:
            return 0
        return (y + self.center[1]) & 0xFF

    def load(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        w1 = self.rom.readWord()
        b = self.rom.readByte()
        w2 = self.rom.readWord()

        self.size = w1 >> 15
        self.unknown = (w1 >> 9) & 0x3F
        self.lowerX = w1 & 0x1FF
        self.highX = (w1 & 0x100) >> 8
        self.realX = self.fixX(self.lowerX, self.highX)
        self.y = b
        self.realY = self.fixY(self.y)
        self.xFlip = (w2 >> 14) & 1
        self.yFlip = w2 >> 15
        self.priority = (w2 >> 12) & 0b11
        self.palette = (w2 >> 9) & 0b111
        self.tile = w2 & 0x1FF

        self.raw = "{} {} {}".format(hex(w1), hex(b), hex(w2))

    def getRaw(self):
        w1 = (self.size << 15) | (self.unknown << 9) | self.lowerX
        b = self.y
        w2 = (self.yFlip << 15) | (self.xFlip << 14) | (self.priority << 12) | (self.palette << 9) | self.tile

        return (w1, b, w2)

    def write(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        w1, b, w2 = self.getRaw()
        self.rom.writeWord(w1)
        self.rom.writeByte(b)
        self.rom.writeWord(w2)

    def transform(self, transformation):
        width = 8 if self.size == 0 else 16
        if transformation == Transform.Mirror:
            self.xFlip = 1 - self.xFlip
            x = Int2(self.lowerX, 0x1FF)
            x.mul(-1)
            x.sub(width)
            self.lowerX = x.get()
            self.highX = (self.lowerX & 0x100) >> 8

    def displayASM(self):
        w1, b, w2 = self.getRaw()
        return "dw ${:04x} : db ${:02x} : dw ${:04x}".format(w1, b, w2)

    def debug(self):
        self.log.debug("OAM at {} size: {} x: {:3} y: {:3} Xflip: {} Yflip: {} priority: {} palette: {} tile: {:3} raw: {}".format(self.dataAddr, self.size, self.realX, self.realY, self.xFlip, self.yFlip, self.priority, self.palette, self.tile, self.raw))

class EnemyHeader(object):
    def __init__(self, rom, dataAddr):
        self.log = logger
        self.rom = rom
        self.dataAddr = dataAddr
        self.load()

    def load(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        self.tileDataSize = self.rom.readWord()
        self.palette = self.rom.readWord()
        self.health = self.rom.readWord()
        self.damage = self.rom.readWord()
        self.xRadius = self.rom.readWord()
        self.yRadius = self.rom.readWord()
        self.bank = self.rom.readByte()
        self.hurtAiTime = self.rom.readByte()
        self.cry = self.rom.readWord()
        self.bossValue = self.rom.readWord()
        self.initAi = self.rom.readWord()
        self.numberParts = self.rom.readWord()
        self.rom.readWord()
        self.mainAi = self.rom.readWord()
        self.grappleAi = self.rom.readWord()
        self.hurtAi = self.rom.readWord()
        self.frozenAi = self.rom.readWord()
        self.XrayAi = self.rom.readWord()
        self.deathAnim = self.rom.readWord()
        self.rom.readWord()
        self.rom.readWord()
        self.pbReaction = self.rom.readWord()
        self.rom.readWord()
        self.rom.readWord()
        self.rom.readWord()
        self.enemyTouch = self.rom.readWord()
        self.enemyShot = self.rom.readWord()
        self.rom.readWord()
        self.tileData = self.rom.readBytes(3)
        self.layer = self.rom.readByte()
        self.dropChance = self.rom.readWord()
        self.vulnerabilities = self.rom.readWord()
        self.enemyName = self.rom.readWord()

    def debug(self):
        self.log.debug("")
        self.log.debug("enemy: {}".format(hex(self.enemyId)))
        for key, value in self.__dict__.items():
            if key == 'rom':
                continue
            self.log.debug("{}: {}".format(key, hex(value)))

class Transform(Enum):
    Mirror = 0

class Room(object):
    def __init__(self, rom, dataAddr):
        self.log = logger
        self.rom = rom
        self.dataAddr = dataAddr
        self.load()

    def load(self):
        self.log.debug("* load room header")
        self.loadHeader()
        self.log.debug("* load state headers")
        self.loadStateHeaders()
        self.log.debug("* load states")
        self.loadStates()
        self.log.debug("* load enemies")
        self.loadEnemies()
        self.log.debug("* load PLMs")
        self.loadPLMs()
        self.log.debug("* load VARIA area")
        self.loadVariaArea()
        self.log.debug("* load layouts")
        self.loadLayout()
        self.log.debug("* load scrolls")
        self.loadScrolls()
        self.log.debug("* load doors")
        self.loadDoors()
        self.log.debug("")

    def loadHeader(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        self.roomIndex = self.rom.readByte()
        self.area = self.rom.readByte()
        self.mapX = self.rom.readByte()
        self.mapY = self.rom.readByte()
        self.width = self.rom.readByte()
        self.height = self.rom.readByte()
        self.upScroller = self.rom.readByte()
        self.downScroller = self.rom.readByte()
        self.specialGfxBitflag = self.rom.readByte()
        # LoROM address
        self.doorsPtr = 0x8F0000 + self.rom.readWord()
        self.log.debug("room: {}".format(hex(self.dataAddr)))

    def loadStateHeaders(self):
        self.roomStateHeaders = []
        roomStateHeader = RoomStateHeader(self.rom, pc_to_snes(self.rom.tell()))
        self.log.debug("state header: {} type: {}".format(hex(roomStateHeader.dataAddr), hex(roomStateHeader.headerType)))
        assert roomStateHeader.headerType != 0, "state type is 0"
        self.roomStateHeaders.append(roomStateHeader)
        while roomStateHeader.headerType != StateType.Standard:
            roomStateHeader = RoomStateHeader(self.rom, pc_to_snes(self.rom.tell()))
            assert roomStateHeader.headerType != 0, "state type is 0"
            self.log.debug("state header: {} type: {}".format(hex(roomStateHeader.dataAddr), hex(roomStateHeader.headerType)))
            self.roomStateHeaders.append(roomStateHeader)

    def loadStates(self):
        self.roomStates = {}
        for roomStateHeader in self.roomStateHeaders:
            self.log.debug("state: {}".format(hex(roomStateHeader.roomStatePtr)))
            roomState = RoomState(self.rom, roomStateHeader.roomStatePtr)
            self.roomStates[roomStateHeader.roomStatePtr] = roomState
            # choose one of the standard state as the state we're going to use
            if roomStateHeader.headerType == StateType.Standard:
                self.defaultRoomState = roomState

    def loadEnemies(self):
        # loop on room state then on enemy set
        self.enemies = {}
        self.enemyHeaders = {}

        for state in self.roomStates.values():
            if state.enemySetPtr in self.enemies:
                continue

            self.enemies[state.enemySetPtr] = []

            self.rom.seek(snes_to_pc(state.enemySetPtr))
            enemyId = 0
            for _ in range(32):
                enemyId = self.rom.readWord()
                if enemyId == 0xFFFF:
                    break
                self.enemies[state.enemySetPtr].append(EnemyInstance(self.rom, enemyId, pc_to_snes(self.rom.tell()-2)))

            for enemy in self.enemies[state.enemySetPtr]:
                if enemy.enemyId not in self.enemyHeaders:
                    self.enemyHeaders[enemy.enemyId] = EnemyHeader(self.rom, 0xA00000+enemyId)

        for setPtr, enemies in self.enemies.items():
            self.log.debug("enemies in set {}: {}".format(hex(setPtr), [hex(enemy.enemyId) for enemy in enemies]))

    def loadPLMs(self):
        # loop on room state then on plm set
        self.plms = {}

        for state in self.roomStates.values():
            if state.plmSetPtr in self.plms:
                continue

            self.plms[state.plmSetPtr] = []

            self.rom.seek(snes_to_pc(state.plmSetPtr))
            plmId = 0
            for i in range(40):
                plmId = self.rom.readWord()
                if plmId == 0x0000:
                    break
                self.plms[state.plmSetPtr].append(PLM.factory(self.rom, plmId))

        for setPtr, plms in self.plms.items():
            self.log.debug("plms in set {}: {}".format(hex(setPtr), [hex(plm.plmId) for plm in plms]))

    def loadVariaArea(self):
        for roomStateHeader in self.roomStateHeaders:
            # only standard state
            if roomStateHeader.headerType == StateType.Standard:
                self.variaArea = self.roomStates[roomStateHeader.roomStatePtr].unusedPtr & 0xffff

    def loadLayout(self):
        self.levelData = {}
        for state in self.roomStates.values():
            if state.levelDataPtr in self.levelData:
                continue
            self.log.debug("level data set: {}".format(hex(state.levelDataPtr)))
            self.levelData[state.levelDataPtr] = LevelData(self.rom, state.levelDataPtr, (self.width, self.height), self.plms)

    def loadScrolls(self):
        self.scrolls = {}
        for state in self.roomStates.values():
            if state.scrollSetPtr in self.scrolls:
                continue
            self.scrolls[state.scrollSetPtr] = Scroll(self.rom, state.scrollSetPtr, (self.width, self.height))

        for setPtr, scroll in self.scrolls.items():
            self.log.debug("scrolls in set {}: {}".format(hex(setPtr), scroll.screens))

    def loadDoors(self):
        # load all ROM doors to get these pointing to the room
        doorsSetsAddr = [(0x8388FE, 0x839AC2), (0x83A18C, 0x83ABF0)]
        doorSize = 12
        doors = defaultdict(list)
        for (start, end) in doorsSetsAddr:
            self.rom.seek(snes_to_pc(start))
            for i in range((end-start)//doorSize):
                self.rom.seek(start + i*doorSize)
                doorId = pc_to_snes(self.rom.tell()) & 0xffff
                door = Door(self.rom, doorId)
                doors[door.destRoom & 0xffff].append(door)

        self.doors = doors[self.dataAddr & 0xffff]

        self.log.debug("doors: {}".format([hex(door.doorId) for door in self.doors]))

    def write(self):
        self.log.debug("* write room header")
        self.writeHeader()
        self.log.debug("* write state headers")
        self.writeStateHeaders()
        self.log.debug("* write states")
        self.writeStates()
        self.log.debug("* write enemies")
        self.writeEnemies()
        self.log.debug("* write PLMs")
        self.writePLMs()
        self.log.debug("* write layouts")
        self.writeLayout()
        self.log.debug("* write scrolls")
        self.writeScrolls()
        self.log.debug("* write doors")
        self.writeDoors()

    def writeHeader(self):
        self.rom.seek(snes_to_pc(self.dataAddr))

        self.rom.writeByte(self.roomIndex)
        self.rom.writeByte(self.area)
        self.rom.writeByte(self.mapX)
        self.rom.writeByte(self.mapY)
        self.rom.writeByte(self.width)
        self.rom.writeByte(self.height)
        self.rom.writeByte(self.upScroller)
        self.rom.writeByte(self.downScroller)
        self.rom.writeByte(self.specialGfxBitflag)
        self.rom.writeWord(self.doorsPtr & 0xffff)

    def writeStateHeaders(self):
        for roomStateHeader in self.roomStateHeaders:
            roomStateHeader.write()

    def writeStates(self):
        for state in self.roomStates.values():
            state.write()

    def writeEnemies(self):
        for enemies in self.enemies.values():
            for enemy in enemies:
                enemy.write()

    def writePLMs(self):
        for plmSetPtr, plms in self.plms.items():
            self.rom.seek(snes_to_pc(plmSetPtr))
            for plm in plms:
                plm.write()

    def writeLayout(self):
        for layout in self.levelData.values():
            layout.unload()
            layout.write()

    def writeScrolls(self):
        for scroll in self.scrolls.values():
            scroll.write()

    def writeDoors(self):
        for door in self.doors:
            door.write()

    def transform(self, transformation):
        if transformation == Transform.Mirror:
            self.log.debug("* transform room: {}".format(transformation))

            # enemies
            for enemySetPtr, enemies in self.enemies.items():
                for enemy in enemies:
                    enemy.transform(transformation, (self.width, self.height))

            # plms
            for plmSetPtr, plms in self.plms.items():
                self.rom.seek(snes_to_pc(plmSetPtr))
                for plm in plms:
                    plm.transform(transformation, (self.width, self.height))

            # scrolls
            for scroll in self.scrolls.values():
                scroll.transform(transformation, (self.width, self.height))

            # doors
            for door in self.doors:
                door.transform(transformation, (self.width, self.height))

            # layout
            for layout in self.levelData.values():
                layout.transform(transformation, (self.width, self.height))

        else:
            raise Exception("{} not implemented".format(transformation))

        self.log.debug("")

class StateType:
    Standard = 0xE5E6
    Events = 0xE612
    Bosses = 0xE629
    TourianBoss = 0xE5FF
    Morph = 0xE640
    MorphMissiles = 0xE652
    PowerBombs = 0xE669
    SpeedBooster = 0xE676

class RoomStateHeader(object):
    sizeStandard = 2
    sizeEvents = 5
    sizeItems = 4

    def __init__(self, rom, dataAddr):
        self.rom = rom
        self.dataAddr = dataAddr
        self.load()

    def load(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        self.headerType = self.rom.readWord()
        self.value = 0

        if self.headerType == StateType.Standard:
            self.roomStatePtr = self.dataAddr+2
        elif self.headerType in [StateType.Events, StateType.Bosses]:
            self.value = self.rom.readByte()
            self.roomStatePtr = 0x8F0000 + self.rom.readWord()
        else:
            self.roomStatePtr = 0x8F0000 + self.rom.readWord()

    def size(self):
        if self.headerType == StateType.Standard:
            return RoomStateHeader.sizeStandard
        elif self.headerType in [StateType.Events, StateType.Bosses]:
            return RoomStateHeader.sizeEvents
        else:
            return RoomStateHeader.sizeItems

    def write(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        self.rom.writeWord(self.headerType)

        if self.headerType == StateType.Standard:
            pass
        elif self.headerType in [StateType.Events, StateType.Bosses]:
            self.rom.writeByte(self.value)
            self.rom.writeWord(self.roomStatePtr & 0xffff)
        else:
            self.rom.writeWord(self.roomStatePtr & 0xffff)

class RoomState(object):
    def __init__(self, rom, dataAddr):
        self.log = logger
        self.rom = rom
        self.dataAddr = dataAddr
        self.load()

    def load(self):
        self.rom.seek(snes_to_pc(self.dataAddr))

        self.levelDataPtr        = self.rom.readBytes(3)
        self.tileSet             = self.rom.readByte()
        self.songSet             = self.rom.readByte()
        self.playIndex           = self.rom.readByte()
        self.fxPtr               = 0x830000 + self.rom.readWord()
        self.enemySetPtr         = 0xA10000 + self.rom.readWord()
        self.enemyGfxPtr         = 0xB40000 + self.rom.readWord()
        self.backgroundScrolling = self.rom.readWord()
        self.scrollSetPtr        = 0x8F0000 + self.rom.readWord()
        self.unusedPtr           = 0x8F0000 + self.rom.readWord() # in VARIA store area id
        self.mainAsmPtr          = 0x8F0000 + self.rom.readWord()
        self.plmSetPtr           = 0x8F0000 + self.rom.readWord()
        self.backgroundPtr       = 0x8F0000 + self.rom.readWord()
        self.setupAsmPtr         = 0x8F0000 + self.rom.readWord()

    def write(self):
        self.rom.seek(snes_to_pc(self.dataAddr))

        self.rom.writeBytes(self.levelDataPtr, 3)
        self.rom.writeByte(self.tileSet)
        self.rom.writeByte(self.songSet)
        self.rom.writeByte(self.playIndex)
        self.rom.writeWord(self.fxPtr & 0xffff)
        self.rom.writeWord(self.enemySetPtr  & 0xffff)
        self.rom.writeWord(self.enemyGfxPtr  & 0xffff)
        self.rom.writeWord(self.backgroundScrolling)
        self.rom.writeWord(self.scrollSetPtr & 0xffff)
        self.rom.writeWord(self.unusedPtr   & 0xffff)
        self.rom.writeWord(self.mainAsmPtr  & 0xffff)
        self.rom.writeWord(self.plmSetPtr   & 0xffff)
        self.rom.writeWord(self.backgroundPtr  & 0xffff)
        self.rom.writeWord(self.setupAsmPtr  & 0xffff)

class EnemyInstance(object):
    def __init__(self, rom, enemyId, dataAddr):
        self.log = logger
        self.rom = rom
        self.enemyId = enemyId
        self.dataAddr = dataAddr
        self.load()

    def load(self):
        # offset in rom is already set to after id
        self.Xpos = self.rom.readWord()
        self.Ypos = self.rom.readWord()
        self.initParam = self.rom.readWord()
        self.properties = self.rom.readWord()
        self.extraProperties = self.rom.readWord()
        self.param1 = self.rom.readWord()
        self.param2 = self.rom.readWord()

    def write(self):
        self.rom.seek(snes_to_pc(self.dataAddr))

        self.rom.writeWord(self.enemyId)
        self.rom.writeWord(self.Xpos)
        self.rom.writeWord(self.Ypos)
        self.rom.writeWord(self.initParam)
        self.rom.writeWord(self.properties)
        self.rom.writeWord(self.extraProperties)
        self.rom.writeWord(self.param1)
        self.rom.writeWord(self.param2)

    def transform(self, transformation, size):
        # enemies positions are in pixel unit
        width = size[0] * 256
        height = size[1] * 256
        if transformation == Transform.Mirror:
            self.log.debug("enemy {}: old x: {} new x: {}".format(hex(self.enemyId), hex(self.Xpos), hex(width - self.Xpos)))
            self.Xpos = width - self.Xpos

class PLM(object):
    @staticmethod
    def factory(rom, plmId):
        if hex(plmId).lower() in RomReader.items:
            return PLMItem(rom, plmId)
        elif plmId in doorPlms:
            return PLMDoor(rom, plmId)
        else:
            return PLM(rom, plmId)

    def __init__(self, rom, plmId):
        self.log = logger
        self.rom = rom
        self.plmId = plmId
        self.load()

    def load(self):
        # offset in rom is already set to after id
        self.Xpos = self.rom.readByte()
        self.Ypos = self.rom.readByte()
        self.plmParam = self.rom.readWord()
        self.width = 1

    def write(self):
        # offset in rom is already set to plm start
        self.rom.writeWord(self.plmId)
        self.rom.writeByte(self.Xpos)
        self.rom.writeByte(self.Ypos)
        self.rom.writeWord(self.plmParam)

    def transform(self, transformation, size):
        # plm unit is tiles
        width = size[0] * 16
        height = size[1] * 16
        if transformation == Transform.Mirror:
            self.Xpos = width - self.Xpos - self.width

class PLMDoor(PLM):
    def transform(self, transformation, size):
        # up/down doors are wider
        facing = plmFacing[self.plmId]
        if facing in (Facing.Top, Facing.Bottom):
            self.width = 4

        super().transform(transformation, size)

        if transformation == Transform.Mirror:
            # also change facing
            if facing in (Facing.Left, Facing.Right):
                facing = indicatorsDirection[facing]

                for plmColor in plmRed, plmGreen, plmYellow, plmGrey:
                    if self.plmId in plmColor:
                        self.plmId = plmColor[facing]

class PLMItem(PLM):
    pass

class Mode(IntFlag):
    _16 = auto()
    _8 = auto()

# REP #$10 Sets X and Y to 16-bit mode
# REP #$20 Sets A to 16-bit mode
# REP #$30 Sets A, X and Y to 16-bit mode
# SEP #$10 Sets X and Y to 8-bit mode
# SEP #$20 Sets A to 8-bit mode
# SEP #$30 Sets A, X and Y to 8-bit mode
class CPUState(object):
    def __init__(self):
        self.mi_flag = Mode._16
        self.ma_flag = Mode._16

    def sep(self, newState):
        if newState & 0x10:
            self.mi_flag = Mode._8
        if newState & 0x20:
            self.ma_flag = Mode._8

    def rep(self, newState):
        if newState & 0x10:
            self.mi_flag = Mode._16
        if newState & 0x20:
            self.ma_flag = Mode._16

class Opcode(object):
    @staticmethod
    def factory(rom, cpuState):
        opcode = rom.readByte()
        if opcode == 0x08:
            return PHP()
        elif opcode == 0xE2:
            newState = rom.readByte()
            cpuState.sep(newState)
            return SEP(newState)
        elif opcode == 0xA9:
            if cpuState.ma_flag == Mode._16:
                return LDA_IMM(rom.readWord(), cpuState.ma_flag)
            else:
                return LDA_IMM(rom.readByte(), cpuState.ma_flag)
        elif opcode == 0x8F:
            return STA_LONG(rom.readLong())
        elif opcode == 0x28:
            return PLP()
        elif opcode == 0x60:
            return RTS()
        else:
            return UNKNOWN(opcode)

class PHP(Opcode):
    opcode = 0x08
    def write(self, rom):
        rom.writeByte(PHP.opcode)
    def display(self):
        return "PHP"

class SEP(Opcode):
    opcode = 0xE2
    def __init__(self, value):
        self.value = value
    def write(self, rom):
        rom.writeByte(SEP.opcode)
        rom.writeByte(self.value)
    def display(self):
        return "SEP #${:02x}".format(self.value)

class LDA_IMM(Opcode):
    opcode = 0xA9
    def __init__(self, value, flag):
        self.value = value
        self.flag = flag
    def write(self, rom):
        rom.writeByte(LDA_IMM.opcode)
        if self.flag == Mode._16:
            rom.writeWord(self.value)
        else:
            rom.writeByte(self.value)
    def display(self):
        if self.flag == Mode._16:
            return "LDA #${:04x}".format(self.value)
        else:
            return "LDA #${:02x}".format(self.value)

class STA_LONG(Opcode):
    opcode = 0x8F
    def __init__(self, value):
        self.value = value
    def write(self, rom):
        rom.writeByte(STA_LONG.opcode)
        rom.writeLong(self.value)
    def display(self):
        return "STA ${:06x}".format(self.value)

class PLP(Opcode):
    opcode = 0x28
    def write(self, rom):
        rom.writeByte(PLP.opcode)
    def display(self):
        return "PLP"

class RTS(Opcode):
    opcode = 0x60
    def write(self, rom):
        rom.writeByte(RTS.opcode)
    def display(self):
        return "RTS"

class UNKNOWN(Opcode):
    def __init__(self, opcode):
        self.opcode = opcode
    def display(self):
        return "Unknown opcode: {:02x}".format(self.opcode)

class Orientation(IntEnum):
    Right_open = 0x00
    Right_close = 0x04
    Left_open = 0x01
    Left_close = 0x05
    Down_open = 0x02
    Down_close = 0x06
    Up_open = 0x03
    Up_close = 0x07

class Door(object):
    def __init__(self, rom, doorId):
        self.log = logger
        self.rom = rom
        self.doorId = doorId
        self.dataAddr = 0x830000 + self.doorId
        self.load()

    def load(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        self.destRoom = 0x8f0000 + self.rom.readWord()
        self.elevatorProperties = self.rom.readByte()
        self.orientation = self.rom.readByte()
        self.capX = self.rom.readByte()
        self.capY = self.rom.readByte()
        self.screenX = self.rom.readByte()
        self.screenY = self.rom.readByte()
        self.distanceToSpawn = self.rom.readWord()
        self.customASM = 0x8f0000 + self.rom.readWord()

        if self.orientation in (Orientation.Down_open, Orientation.Down_close,
                                Orientation.Up_open, Orientation.Up_close):
            self.width = 4
        else:
            self.width = 1

        if self.customASM != 0x8f0000:
            self.loadASM()

    def loadASM(self):
        self.asm = []
        self.rom.seek(snes_to_pc(self.customASM))

        self.validASM = False
        cpuState = CPUState()

        while True:
            self.asm.append(Opcode.factory(self.rom, cpuState))

            # read until RTS
            if type(self.asm[-1]) is RTS:
                self.validASM = True
                break
            elif type(self.asm[-1]) is UNKNOWN:
                self.validASM = False
                break

    def write(self):
        self.rom.seek(snes_to_pc(self.dataAddr))

        self.rom.writeWord(self.destRoom & 0xffff)
        self.rom.writeByte(self.elevatorProperties)
        self.rom.writeByte(self.orientation)
        self.rom.writeByte(self.capX)
        self.rom.writeByte(self.capY)
        self.rom.writeByte(self.screenX)
        self.rom.writeByte(self.screenY)
        self.rom.writeWord(self.distanceToSpawn)
        self.rom.writeWord(self.customASM & 0xffff)

        if self.customASM != 0x8f0000 and self.validASM:
            self.writeASM()

    def writeASM(self):
        self.rom.seek(snes_to_pc(self.customASM))
        for opcode in self.asm:
            opcode.write(self.rom)

    def displayASM(self):
        if self.customASM != 0x8f0000:
            out = ""
            out += "org ${:06x}\n".format(self.customASM)
            for opcode in self.asm:
                out += "    {}\n".format(opcode.display())
            return (self.customASM, out)
        else:
            return (None, None)

    def transform(self, transformation, size):
        # cap unit is tiles
        width = size[0] * 16
        height = size[1] * 16
        if transformation == Transform.Mirror:
            self.screenX = size[0] - self.screenX - 1
            self.capX = width - self.capX - self.width

        # $7E:CD20..51: Scrolls
        #     0: Red. Cannot scroll into this area
        #     1: Blue. Hides the bottom 2 rows of the area
        #     2: Green. Unrestricted
        if self.customASM != 0x8f0000 and self.validASM:
            scrolls_start = 0x7ECD20
            for opcode in self.asm:
                if type(opcode) == STA_LONG:
                    screen = opcode.value - scrolls_start
                    x = screen % size[0]
                    y = screen // size[0]
                    if transformation == Transform.Mirror:
                        x = size[0] - x - 1
                        screen = x + y * size[0]
                    opcode.value = scrolls_start + screen

class Scroll(object):
    def __init__(self, rom, dataAddr, size):
        self.log = logger
        self.rom = rom
        self.dataAddr = dataAddr
        # (screens X, screens Y)
        self.size = size
        self.load()

    def load(self):
        self.rom.seek(snes_to_pc(self.dataAddr))
        self.screens = []
        for _ in range(self.size[0] * self.size[1]):
            self.screens.append(self.rom.readByte())

    def write(self):
        self.rom.seek(snes_to_pc(self.dataAddr))

        for scroll in self.screens:
            self.rom.writeByte(scroll)

    def transform(self, transformation, size):
        # scroll unit is screens
        width = size[0]
        height = size[1]
        if transformation == Transform.Mirror:
            transScreens = [0]*(width * height)
            for x in range(width):
                for y in range(height):
                    transScreens[x + y*width] = self.screens[(width - x - 1) + y*width]
            self.screens = transScreens

class LevelData(object):
    def __init__(self, rom, dataAddr, size, plms=None):
        self.log = logger
        self.rom = rom
        self.dataAddr = dataAddr
        # [width, height] in screens
        self.size = size
        self.plms = plms
        self.screenCount = 0

        self.layer1 = []
        self.layer2 = []
        self.bts = []

        self.loaded = False
        self.compressedSize = 0
        self.rawData = []

        self.load()

    def _concatBytes(self, b0, b1):
        return b0 + (b1 << 8)

    def _unconcatWord(self, w):
        return [w & 0x00FF, (w & 0xFF00) >> 8]

    def debug(self):
        self.log.debug("compressedSize: {}".format(self.compressedSize))
        self.log.debug("decompressedSize: {}".format(self.decompressedSize))
        self.log.debug("screenCount: {}".format(self.screenCount))
        self.log.debug("layer1Size: {}".format(self.layer1Size))
        self.log.debug("btsSize: {}".format(self.btsSize))
        self.log.debug("layer2Size: {}".format(self.layer2Size))
        self.log.debug("len layer1: {}".format(len(self.layer1)))
        self.log.debug("len bts: {}".format(len(self.bts)))
        self.log.debug("len layer2: {}".format(len(self.layer2)))

    def displayLayoutTile(self, t, displayBts=True):
        tile = t & 0x3FF
        hflip = (t >> 10) & 1
        vflip = (t >> 11) & 1
        btsType = (t >> 12) & 0xF
        if displayBts:
            return "{:3}|{}|{}|{}".format(hex(tile)[2:], hflip, vflip, hex(btsType)[2:])
        else:
            return "{:2}|{}|{}  ".format(hex(tile)[2:], hflip, vflip)

    def displayScreen(self, screen):
        x, y = screen
        # a screen is 16x16 tiles
        base = x * 16 + y * self.size[0] * 256
        nextRow = self.size[0] * 16
        self.log.debug("layer1:")
        for i in range(16):
            rowBase = base + i * nextRow
            self.log.debug(["{}/{:3}".format(self.displayLayoutTile(t), b) for (t, b) in zip(self.layer1[rowBase:rowBase+16], self.bts[rowBase:rowBase+16])])

    def displayBts(self, b):
        if b >= 0x80:
            vFlip = 1
            b ^= 0x80
        else:
            vFlip = 0
        if b >= 0x40:
            hFlip = 1
            b ^= 0x40
        else:
            hFlip = 0
        return "{:2}|{}|{}".format(hex(b)[2:], hFlip, vFlip)

    def displaySubScreen(self, screen, boundingRect):
        x, y = screen
        # display only boundingRect of screen (x,y), display only bts
        base = x * 16 + y * self.size[0] * 256
        nextRow = self.size[0] * 16
        self.log.debug("subscreen: (bts value|hFlip|vFlip|type)")
        self.log.debug("subscreen: (layout tile|hFlip|vFlip)")
        for i in range(16):
            rowBase = base + i * nextRow
            self.log.debug(" ".join(["{}|{:1}".format(self.displayBts(b), hex((t >> 12) & 0xF)[2:]) if boundingRect.isInside(j, i) else "    .   " for j, (t, b) in enumerate(zip(self.layer1[rowBase:rowBase+16], self.bts[rowBase:rowBase+16]))]))
            self.log.debug(" ".join([self.displayLayoutTile(t, displayBts=False) if boundingRect.isInside(j, i) else "    .   " for j, t in enumerate(self.layer1[rowBase:rowBase+16])]))

    def load(self):
        data = Compressor().decompress(self.rom, snes_to_pc(self.dataAddr))
        self.compressedSize = data[0]
        self.log.debug("compressed data size: {}".format(self.compressedSize))
        self.rawData = data[1]
        self.decompressedSize = len(self.rawData)
        self.log.debug("uncompressed data size: {}".format(self.decompressedSize))

        self.layer1Size = self._concatBytes(self.rawData[0], self.rawData[1])
        self.btsSize = int(self.layer1Size / 2)
        if self.layer1Size + self.btsSize + 2 < self.decompressedSize:
            self.layer2Size = self.layer1Size
        else:
            self.layer2Size = 0
        self.screenCount = int(self.btsSize / 256)

        self.log.debug("size layer1: {} bts: {} layer2: {}".format(self.layer1Size, self.btsSize, self.layer2Size))

        if self.layer1Size + self.btsSize + self.layer2Size + 2 != self.decompressedSize:
            self.log.warning("wrong decompressed data size")
            if self.decompressedSize == self.layer1Size + self.btsSize + self.layer2Size + 2 - 1:
                self.log.debug("add missing byte in layer2 data")
                self.rawData.append(0x3)

        # validate that raw data is ok
        if self.log.getEffectiveLevel() == logging.DEBUG:
            for i, r in enumerate(self.rawData):
                if r >= 0x100:
                    assert False, "byte in rawData at {} is too big: {}".format(i, hex(r))

        layer1Counter = 2
        btsCounter = 2 + self.layer1Size
        layer2Counter = 2 + self.layer1Size + self.btsSize

        for i in range(self.btsSize):
            self.layer1.append(self._concatBytes(self.rawData[layer1Counter], self.rawData[layer1Counter+1]))
            self.bts.append(self.rawData[btsCounter])
            if self.layer2Size > 0:
                self.layer2.append(self._concatBytes(self.rawData[layer2Counter], self.rawData[layer2Counter+1]))

            layer1Counter += 2
            btsCounter += 1
            layer2Counter += 2

        self.log.debug("len layer1: {} bts: {} layer2: {}".format(len(self.layer1), len(self.bts), len(self.layer2)))

    def unload(self):
        # rebuild raw data
        rawData = []
        rawData += self._unconcatWord(self.layer1Size)
        # transform back from word to byte
        self.log.debug("layer1 size: {}".format(self.layer1Size))
        self.log.debug("len(layer1): {}".format(len(self.layer1)))

        for word in self.layer1:
            rawData += self._unconcatWord(word)

        self.log.debug("len rawData with layer1: {}".format(len(rawData)))

        rawData += self.bts

        for word in self.layer2:
            rawData += self._unconcatWord(word)

        self.log.debug("len rawData: {}".format(len(rawData)))
        return rawData

    def write(self, vanillaSize=None):
        if vanillaSize is None:
            vanillaSize = self.compressedSize

        # rebuild raw data
        rawData = self.unload()
        self.log.debug("rawData len: {}".format(len(rawData)))

        # recompress data
        compressedData = Compressor(profile='Slow').compress(rawData)
        recompressedDataSize = len(compressedData)
        self.log.debug("compressedData len: {} (old: {}, vanilla: {})".format(recompressedDataSize, self.compressedSize, vanillaSize))
        assert recompressedDataSize <= vanillaSize
        # write compress data
        self.rom.seek(snes_to_pc(self.dataAddr))
        for byte in compressedData:
            self.rom.writeByte(byte)

    def copyLayout(self, screen, boundingRect):
        # copy layer1 and bts
        x, y = screen
        layer1 = []
        bts = []
        start = boundingRect.start()
        width = boundingRect.width()
        height = boundingRect.height()
        base = x * 16 + start[0] + y * self.size[0] * 256 + start[1] * self.size[0] * 16
        nextRow = self.size[0] * 16

        boundingRect.debug()
        self.log.debug("copy: start: {} width: {} height: {}".format(start, width, height))

        for i in range(height):
            rowBase = base + i * nextRow
            layer1 += self.layer1[rowBase:rowBase+width]
            bts += self.bts[rowBase:rowBase+width]

        self.displayCopy(layer1, bts, boundingRect)

        return (layer1, bts)

    def displayCopy(self, layer1, bts, boundingRect):
        width = boundingRect.width()
        height = boundingRect.height()
        for i in range(height):
            print(" ".join(["{}|{:1}".format(self.displayBts(b), hex((t >> 12) & 0xF)[2:]) for (t, b) in zip(layer1[i*width:(i+1)*width], bts[i*width:(i+1)*width])]))
            print(" ".join([self.displayLayoutTile(t, displayBts=False) for t in layer1[i*width:(i+1)*width]]))


    def pasteLayout(self, data, screen, boundingRect):
        x, y = screen
        layer1 = data[0]
        bts = data[1]
        start = boundingRect.start()
        width = boundingRect.width()
        height = boundingRect.height()

        base = x * 16 + start[0] + y * self.size[0] * 256 + start[1] * self.size[0] * 16
        nextRow = self.size[0] * 16

        boundingRect.debug()
        self.log.debug("paste: start: {} width: {} height: {}".format(start, width, height))

        for i in range(height):
            rowBase = base + i * nextRow
            for j in range(width):
                self.layer1[rowBase+j] = layer1[i*width+j]
                self.bts[rowBase+j] = bts[i*width+j]

    def emptyLayout(self, screen, boundingRect):
        x, y = screen
        start = boundingRect.start()
        width = boundingRect.width()
        height = boundingRect.height()

        defaultLayer = 0xFF
        defaultBts = 0x00

        base = x * 16 + start[0] + y * self.size[0] * 256 + start[1] * self.size[0] * 16
        nextRow = self.size[0] * 16

        boundingRect.debug()
        self.log.debug("empty: start: {} width: {} height: {}".format(start, width, height))

        for i in range(height):
            rowBase = base + i * nextRow
            for j in range(width):
                self.layer1[rowBase+j] = defaultLayer
                self.bts[rowBase+j] = defaultBts

    def getTileAddr(self, screen, tx, ty):
        (sx, sy) = screen
        base = sx * 16 + sy * self.size[0] * 256
        nextRow = self.size[0] * 16

        rowBase = base + ty * nextRow
        return rowBase + tx

    def getTileAddrInv(self, i):
        rowLength = self.size[0] * 16
        y = i // rowLength
        sy = y // 16
        ty = y % 16

        x = i % rowLength
        sx = x // 16
        tx = x % 16

        return (sx, sy, tx, ty)

    def getTile(self, screen, tx, ty):
        addr = self.getTileAddr(screen, tx, ty)
        return (self.layer1[addr], self.bts[addr])

    def updateTile(self, screen, tx, ty, newTile, newBTS):
        tileAddr = self.getTileAddr(screen, tx, ty)
        self.layer1[tileAddr] = newTile
        self.bts[tileAddr] = newBTS

    def getModifiedTiles(self, patch):
        modified = set()
        for i, (oTile, pTile) in enumerate(zip(self.layer1, patch.layer1)):
            if oTile != pTile:
                modified.add(i)
        for i, (oBTS, pBTS) in enumerate(zip(self.bts, patch.bts)):
            if oBTS != pBTS:
                modified.add(i)

        ret = []
        for i in modified:
            # transform i into (sx, sy, tx, ty)
            (sx, sy, tx, ty) = self.getTileAddrInv(i)
            ret.append((sx, sy, tx, ty))

        return ret

    def transformPos(self, transformation, sx, sy, tx, ty, screenSize):
        if transformation == Transform.Mirror:
            # in vanilla tile is in screen (0, 0) at pos (6, 6)
            # in mirror  tile is in screen (1, 0) at pos (9, 6)
            # => screen x: width in screens - 1 - vanilla screen x
            # => screen y: the same
            # => tile x: screen width in tiles - 1 - vanilla tile x
            # => tile y: the same
            return (screenSize[0] - 1 - sx, sy,
                    16 - 1 - tx,            ty)

    def transformTile(self, transformation, t):
        tile = t & 0x3FF
        hflip = (t >> 10) & 1
        vflip = (t >> 11) & 1
        btsType = (t >> 12) & 0xF

        if transformation == Transform.Mirror:
            # invert hflip
            hflip = 1 - hflip

        return (tile) | (hflip << 10) | (vflip << 11) | (btsType << 12)

    def transformBts(self, transformation, addr):
        blue_left = 0x40
        blue_right = 0x41
        blue_top = 0x42
        blue_bottom = 0x43
        bts = self.bts[addr]
        if transformation == Transform.Mirror:
            # handle blue doors bts
            if bts == blue_left:
                return blue_right
            elif bts == blue_right:
                return blue_left

            # handle horizontal blue doors
            if bts == 0xFD:
                door = self.bts[addr-3]
                if door in [blue_bottom, blue_top]:
                    return door
            elif bts == 0xFE:
                door = self.bts[addr-2]
                if door in [blue_bottom, blue_top]:
                    return 0xFF
            elif bts == 0xFF:
                door = self.bts[addr-1]
                if door in [blue_bottom, blue_top]:
                    return 0xFE
            elif bts in [blue_top, blue_bottom]:
                return 0xFD

            return bts

    def transform(self, transformation, size):
        # layout unit is tiles
        width = size[0] * 16
        height = size[1] * 16

        if transformation == Transform.Mirror:
            transLayer1 = [0]*len(self.layer1)
            if self.layer2Size != 0:
                transLayer2 = [0]*len(self.layer2)
            transBts = [0]*len(self.bts)

            for sx in range(size[0]):
                for sy in range(size[1]):
                    for y in range(16):
                        for x in range(16):
                            addr = self.getTileAddr((sx, sy), x, y)
                            tile1 = self.layer1[addr]
                            if self.layer2Size != 0:
                                tile2 = self.layer2[addr]
                            bts = self.transformBts(transformation, addr)
                            msx, msy, mx, my = self.transformPos(transformation, sx, sy, x, y, size)
                            newAddr = self.getTileAddr((msx, msy), mx, my)
                            newTile1 = self.transformTile(transformation, tile1)
                            if self.layer2Size != 0:
                                newTile2 = self.transformTile(transformation, tile2)
                            #print("{}: sx: {} sy: {} x: {} y: {} - {}: msx: {} msy: {} mx: {} my: {}".format(addr, sx, sy, x, y, newAddr, msx, msy, mx, my))

                            transLayer1[newAddr] = newTile1
                            transBts[newAddr] = bts
                            if self.layer2Size != 0:
                                transLayer2[newAddr] = newTile2

            self.layer1 = transLayer1
            self.bts = transBts
            if self.layer2Size != 0:
                self.layer2 = transLayer2

        self.updateScrollBts(transformation)

    def updateScrollBts(self, transformation):
        if self.plms is not None:
            scrollPlmId = 0xb703
            # look for scroll plms to update bts around them
            for plmSet, plms in self.plms.items():
                for plm in plms:
                    if plm.plmId == scrollPlmId:
                        if transformation == Transform.Mirror:
                            screen = (plm.Xpos // 16, plm.Ypos // 16)
                            x = plm.Xpos % 16
                            y = plm.Ypos % 16

                            # look for scroll bts on the right of the plm, only in same screen
                            for i in range(1, 16 - x - 1):
                                addr = self.getTileAddr(screen, x+i, y)
                                # bts x pos has been mirrored, so it's from 01 to ff and we want it from ff to 01
                                if self.bts[addr] == i:
                                    self.log.debug("*** update bts of the right {}".format(i))
                                    self.bts[addr] = 0x100 - i
                                else:
                                    break

                            for i in range(1, x + 1):
                                addr = self.getTileAddr(screen, x-i, y)
                                if self.bts[addr] == 0x100 - i:
                                    self.log.debug("*** update bts of the left -{}".format(i))
                                    self.bts[addr] = i
                                else:
                                    break
