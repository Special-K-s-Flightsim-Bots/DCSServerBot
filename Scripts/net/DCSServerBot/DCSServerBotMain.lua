local base  	= _G

-- register dcsbot in the global scope
base.dcsbot = {}

local require	= base.require
local loadfile	= base.loadfile

local lfs		= require('lfs')
local config	= require("DCSServerBotConfig")
local JSON 		= loadfile("Scripts\\JSON.lua")()

package.path  = package.path..";.\\LuaSocket\\?.lua;"
package.cpath = package.cpath..";.\\LuaSocket\\?.dll;"
local socket 	= require("socket")

local dcsbotgui = {}

local function createSimulationFrameHandler()
    local host, port = config.DCS_HOST, config.DCS_PORT
    local ip = socket.dns.toip(host)
    local UDPRecvSocket = socket.udp()
    UDPRecvSocket:setsockname(ip, port)
    UDPRecvSocket:settimeout(0.0001)
    UDPRecvSocket:setoption('reuseaddr', true)

    return function()
        local msg, err
        repeat
            msg, err = UDPRecvSocket:receive()
            if not err then
                local decoded = JSON:decode(msg)
                local commandFunc = dcsbot[decoded.command]
                if commandFunc then
                    commandFunc(decoded)
                end
            end
        until err
    end
end

dcsbotgui.onSimulationFrame = createSimulationFrameHandler()

local function loadFile(path, name, plugin, env)
    if (lfs.attributes(path)) then
        local u, err = loadfile(path)
        if u then
            setfenv(u, env)
            local ok, err = pcall(u)
            if ok then
                print('Loaded '..name..' script for plugin '..plugin)
            else
                print('Failed to load '..name..' script for plugin '..plugin..': '..err)
            end
        else
            print('Failed to load '..name..' script for plugin '..plugin..': '..err)
        end
    end
end

local function loadPlugin(plugin)
    local env = {}
    setmetatable(env, { __index = _G })

    local base_path = lfs.writedir() .. 'Scripts/net/DCSServerBot/' .. plugin
    local command_path = base_path .. '/commands.lua'
    local hook_path = base_path .. '/callbacks.lua'

    loadFile(command_path, "command", plugin, env)
    loadFile(hook_path, "hook", plugin, env)
end

if DCS.isServer() then
	if config.SERVER_USER ~= nil then
		net.set_name(config.SERVER_USER)
	end
	DCS.setUserCallbacks(dcsbotgui)  -- here we set our callbacks
	for file in lfs.dir(lfs.writedir() .. 'Scripts/net/DCSServerBot') do
		if (file ~= '.' and file ~= '..' and file:sub(-4) ~= '.lua') then
			log.write('DCSServerBot', log.DEBUG, 'Loading plugin ' .. file)
			loadPlugin(file)
		end
	end
end