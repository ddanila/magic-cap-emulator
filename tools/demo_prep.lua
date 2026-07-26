-- Prepare a calibrated NVRAM set for the demo recording (see docs/demo.md).
--
-- A fresh machine opens the pen-calibration scene, which is not interesting
-- on video. Calibrating and then exiting MAME is not enough: Magic Cap only
-- flushes its heap on a real suspend, so this taps the three calibration
-- targets and then presses the power button, exactly as a user would. The
-- resulting nvram/ directory boots straight to the welcome scene.
--
-- Usage: run against a fresh -nvram_directory, then reuse that directory for
-- tools/demo_tour.lua.

local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local power_button = ports[":POWER_BUTTON"]:field(0x01)
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
    {1220, function() tap(240, 160) end},   -- dismiss the welcome scene
    {1240, release},
    {1420, function() tap(23, 23) end},     -- calibration target 1
    {1440, release},
    {1620, function() tap(456, 296) end},   -- calibration target 2
    {1640, release},
    {1820, function() tap(240, 160) end},   -- calibration target 3
    {1840, release},
    {2400, function() shot("prep-desk.png") end},
    {4200, function()
        shot("prep-settled.png")
        -- Suspend the way a user does, so the OS flushes its heap and the
        -- next boot resumes instead of re-running calibration.
        power_button:set_value(1)
    end},
    {4230, function() power_button:set_value(0) end},
    {5400, function()
        shot("prep-suspended.png")
        machine:exit()
    end},
}

emu.register_frame_done(function()
    frames = frames + 1
    for _, step in ipairs(steps) do
        if step[1] == frames then step[2]() end
    end
end)
