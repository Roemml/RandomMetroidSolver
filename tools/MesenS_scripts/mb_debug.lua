local function readWord(ramAddr)
  return emu.readWord(ramAddr, emu.memType.workRam)
end

local function snes_to_pc(B)
      local B_1 = B >> 16
      local B_2 = B & 0xFFFF
      -- return 0 if invalid LoROM address
      if(B_1 < 0x80 or B_1 > 0xFFFFFF or B_2 < 0x8000) then
         return 0
      end
      local A_1 = (B_1 - 0x80) >> 1
      -- if B_1 is even, remove most significant bit
      local A_2 = B_2
      if( (B_1 & 1) == 0 ) then
         A_2 = B_2 & 0x7FFF
      end
      --emu.log(string.format("b: %x b1: %x b2: %x a1: %x a2: %x", B, B_1, B_2, A_1, A_2))
      return (A_1 << 16) | A_2
end

local function readROMWord(romAddr)
   -- readWord for prgRom is bugged
   local addr = snes_to_pc(romAddr)
   local l = emu.read(addr, emu.memType.prgRom)
   local h = emu.read(addr+1, emu.memType.prgRom)
   return l + (h << 8)
end
local function readROMByte(romAddr)
   return emu.read(snes_to_pc(romAddr), emu.memType.prgRom)
end

local function complement2int(value, mask)
   -- convert 2 complement negative int to negative int
   test_mask = (mask+1)>>1
   comp_mask = mask

   -- test if negative value
   if(value & test_mask ~= 0) then
      return -(((~value)+1) & comp_mask)
   else
      return value
   end
end

--        $0F78: ID
--        $0F7A: X position
--        $0F7C: X subposition
--        $0F7E: Y position
--        $0F80: Y subposition
--        $0F82: X radius
--        $0F84: Y radius
--        $0F86: Properties (Special in SMILE)
--        $0F88: Extra properties (special GFX bitflag in SMILE)
--        $0F8A: AI handler
--        $0F8C: Health
--        $0F8E: Spritemap pointer
--        $0F90: Timer
--        $0F92: Initialisation parameter (Orientation in SMILE, Tilemaps in RF) / instruction list pointer
--        $0F94: Instruction timer
--        $0F96: Palette index
--        $0F98: VRAM tiles index
--        $0F9A: Layer
--        $0F9C: Flash timer
--        $0F9E: Frozen timer
--        $0FA0: Invincibility timer
--        $0FA2: Shake timer
--        $0FA4: Frame counter
--        $0FA6: Bank
--        $0FA8: AI variable, frequently function pointer
--        $0FAA: AI variable
--        $0FAC: AI variable
--        $0FAE: AI variable
--        $0FB0: AI variable
--        $0FB2: AI variable
--        $0FB4: Parameter 1 (Speed in SMILE)
--        $0FB6: Parameter 2 (Speed2 in SMILE)

local etecoonData = {
      {"Id", addr=0x0F78},
      {"subAI", addr=0x0FF0},
      {"Xpos", addr=0x0F7A},
      {"Ypos", addr=0x0F7E},
      {"AI1", addr=0x0FA8},
      {"AI2", addr=0x0FE8},
      {"InstrList", addr=0x0F92},
      {"Spritemap", addr=0x0F8E},
      {"target X", addr=0x0FB2},
      {"scroll x", addr=0x0911},
      {"phase", addr=0x7800},
      {"IL", addr=0x7e8002},
--      {"var0", addr=0x0FA8},
--      {"var1", addr=0x0FAA},
--      {"var2", addr=0x0FAC},
--      {"var3", addr=0x0FAE},
--      {"var4", addr=0x0FB0},
}

colors = {}
colors[0x000] = 0x30FF4040
colors[0x040] = 0x3040FF40
colors[0x080] = 0x304040FF
colors[0x0c0] = 0x30FFFF40
colors[0x100] = 0x30FF40FF
colors[0x140] = 0x3040FFFF
colors[0x280] = 0x307F4040
colors[0x2c0] = 0x30407F40
colors[0x200] = 0x3040407F
colors[0x240] = 0x307F7F40
colors[0x280] = 0x307F407F
colors[0x2c0] = 0x30407F7F

-- keep history of mb ai change
history = {}
history[0x00] = {ai = 0x0, subai = 0x0, x = 0x0}
history[0x40] = {ai = 0x0, subai = 0x0, x = 0x0}
history["neck"] = 0x0

local X_OFF = 125
local Y_OFF = 10
local BG = 0x80000000
local FG = 0xFFFFFF

local function printMB()
  local x,y=0,0
  local function printVar(var, value, color)
     emu.drawString(x+2, y+2, var .. ":", color, BG, 1)
     emu.drawString(x+2+X_OFF//2, y+2, string.format("%x", value), color, BG, 1)
  end

  for _, offset in ipairs({0x00, 0x40}) do --, 0x80, 0xc0, 0x100, 0x140, 0x180, 0x1c0, 0x200, 0x240, 0x280, 0x2c0}) do
     local color = colors[offset]
     for i,info in pairs(etecoonData) do
        local var = info[1]
        local value = readWord(info.addr+offset)
        printVar(var, value, color)
        if i % 2 == 1 then
           x = X_OFF
        else
           x = 0
           y = y + Y_OFF
        end
     end
     x = 0
     y = y+(Y_OFF*2)

     -- get ai
     local ai = readWord(0x0FA8+offset)
     local subai = readWord(0x0FF0+offset)
     local x = readWord(0x0F7A+offset)

     -- compare with last one, log if different
     if(ai ~= history[offset]["ai"] or subai ~= history[offset]["subai"] or x ~= history[offset]["x"]) then
        emu.log(string.format("%x: ai: %x subai: %x x: %x", offset, ai, subai, x))
        history[offset]["ai"] = ai
        history[offset]["subai"] = subai
        history[offset]["x"] = x
     end

     local mbneckhitboxsegment4xpos = readWord(0x7E805C)
     if(history["neck"] ~= mbneckhitboxsegment4xpos) then
        emu.log(string.format("neck x before: %x after: %x", history["neck"], mbneckhitboxsegment4xpos))
        history["neck"] = mbneckhitboxsegment4xpos
     end

     -- display colored box for mb brain and body
     local x = readWord(0x0F7A+offset)
     local y = readWord(0x0F7E+offset)

     -- camera
     local layer1x = readWord(0x0911)
     local layer1y = readWord(0x0915)

     emu.drawRectangle(x-layer1x, y-layer1y, 8, 8, color, true)
  end
end

ext_colors = {}
ext_colors[0] = 0xC0FF40FF
ext_colors[1] = 0xC040FF40
ext_colors[2] = 0xC04040FF
ext_colors[3] = 0xC0FFFF40
ext_colors[4] = 0xC0FF4040
ext_colors[5] = 0xC040FFFF
ext_colors[6] = 0xC0404088
ext_colors[7] = 0xC0888840
ext_colors[8] = 0xC0FF40FF
ext_colors[9] = 0xC040FF40
ext_colors[10] = 0xC04040FF
ext_colors[11] = 0xC0FFFF40
ext_colors[12] = 0xC0FF4040
ext_colors[13] = 0xC040FFFF
ext_colors[14] = 0xC0404088
ext_colors[15] = 0xC0888840

local function drawSpriteMaps()
   emu.log("drawSpriteMaps")
   -- camera
   local layer1x = readWord(0x0911)
   local layer1y = readWord(0x0915)

   for _, offset in ipairs({0x0, 0x40}) do
      local croc_x = readWord(0x0F7A+offset)
      local croc_y = readWord(0x0F7E+offset)
      local bank = 0xA90000

      -- crocs use extended spritemaps
      local extsm = readWord(0x0F8E+offset) + bank
      local num_ext = readROMWord(extsm)
      --emu.log("")
      emu.log(string.format("extsm: %x with %x parts", extsm, num_ext))

      for i=0,num_ext-1 do
         local x_ext = complement2int(readROMWord(extsm + 2 + i*8), 0xffff)
         local y_ext = complement2int(readROMWord(extsm + 2 + i*8 + 2), 0xffff)
         local sm = readROMWord(extsm + 2 + i*8 + 4) + bank
         local hitbox = readROMWord(extsm + 2 + i*8 + 6) + bank

         local num = complement2int(readROMWord(sm), 0xffff)

         local color = ext_colors[i]

         -- sprite map
         if num < 0 then
            -- emu.log(string.format("sm %d: %x extended tilemap %d", i, sm, num))
         else
            -- emu.log(string.format("sm %d: %x with %x parts", i, sm, num))
            emu.drawString(6*4*i, 200+(offset/8), string.format("%x", sm & 0xFFFF), color & 0x00FFFFFF, BG, 1)

            for j=0,num-1 do
               local w1 = readROMWord(sm + 2 + j*5)
               local b = readROMByte(sm + 2 + j*5 + 2)
               local w2 = readROMWord(sm + 2 + j*5 + 3)

               local x = complement2int(w1 & 0x1FF, 0x1ff)
               local y = complement2int(b, 0xff)
               local size = (w1 & 0x8000) >> 15
               if(size == 0) then
                  size = 8
               else
                  size = 16
               end
               --emu.log(string.format("%x %x %x: x: %d y: %d size: %d", w1, b, w2, x, y, size))
               emu.drawRectangle(croc_x-layer1x+x_ext+x, croc_y-layer1y+y_ext+y+8, size, size, color, false)
            end
         end

--         -- hitbox
--         local num = readROMWord(hitbox)
--         if num > 0 then
--
--            --emu.log(string.format("hitbox %d: %x with %x parts", i, hitbox, num))
--            emu.drawString(6*4*i, 216+(offset/8), string.format("%x", hitbox & 0xFFFF), color & 0x00FFFFFF, BG, 1)
--
--            for j=0,num-1 do
--               local x1 = complement2int(readROMWord(hitbox + 2 + j*12), 0xffff)
--               local y1 = complement2int(readROMWord(hitbox + 4 + j*12), 0xffff)
--               local x2 = complement2int(readROMWord(hitbox + 6 + j*12), 0xffff)
--               local y2 = complement2int(readROMWord(hitbox + 8 + j*12), 0xffff)
--
----               emu.log(string.format("hitbox %d: %x part %d: x1: %d y1: %d x2:", i, hitbox, num))
--               local base_x = croc_x-layer1x+x_ext
--               local base_y = croc_y-layer1y+y_ext+8
--               emu.drawRectangle(base_x+x1, base_y+y1, x2-x1, y2-y1, color, true)
--            end
--         end

      end
   end
end


emu.addEventCallback(printMB, emu.eventType.endFrame)
emu.addEventCallback(drawSpriteMaps, emu.eventType.endFrame)
