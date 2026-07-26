-- Drive the recorded DataRover tour (see docs/demo.md).
--
-- Expects the calibrated NVRAM produced by tools/demo_prep.lua, so the machine
-- boots to the welcome scene instead of pen calibration. Every tap is an
-- absolute touchscreen coordinate in the native 480x320 space; the frame
-- numbers are the settle times Magic Cap's animations actually need, found by
-- stepping the scenario and checking snapshots.
--
-- The snapshots are the acceptance check for the run: if the scenario desyncs,
-- the beats stop matching (g-center and i-closed are the same scene, so their
-- hashes must be equal).

local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0

local function tap(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function release()
    touch_button:set_value(0)
end

local function shot(name)
    machine.screens[":screen"]:snapshot(name)
end

local steps = {
    {1100, function() tap(240, 160) end},   -- wake the welcome scene
    {1130, release},
    {1900, function() shot("a-desk.png") end},
    {2000, function() tap(247, 186) end},   -- Getting Started: STOP
    {2020, release},
    {2300, function() tap(237, 90) end},    -- ...which opens a confirmation
    {2320, release},
    {2700, function() shot("b-clean-desk.png") end},
    {2850, function() tap(100, 292) end},   -- Stamps drawer
    {2870, release},
    {3300, function() shot("c-stamps.png") end},
    {3500, function() tap(397, 49) end},    -- close Stamps
    {3520, release},
    {3750, function() tap(455, 8) end},     -- to the Hallway
    {3770, release},
    {4100, function() shot("d-hallway.png") end},
    {4200, function() tap(450, 253) end},   -- pan to the painting
    {4220, release},
    {4550, function() tap(196, 125) end},   -- tap the painting
    {4570, release},
    {4850, function() shot("e-painting.png") end},
    {4950, function() tap(196, 125) end},   -- the art cycles
    {4970, release},
    {5250, function() tap(450, 253) end},   -- explore further
    {5270, release},
    {5550, function() tap(30, 253) end},    -- and back
    {5570, release},
    {5850, function() tap(455, 8) end},     -- to Downtown
    {5870, release},
    {6200, function() shot("f-downtown.png") end},
    {6300, function() tap(447, 210) end},   -- into the Internet Center
    {6320, release},
    {6750, function() shot("g-center.png") end},
    {6850, function() tap(432, 90) end},    -- the mail rules
    {6870, release},
    {7300, function() shot("h-rules.png") end},
    {7500, function() tap(409, 45) end},    -- close the rules
    {7520, release},
    {7850, function() shot("i-closed.png") end},
    {8000, function() machine:exit() end},
}

emu.register_frame_done(function()
    frames = frames + 1
    for _, step in ipairs(steps) do
        if step[1] == frames then step[2]() end
    end
end)
