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

local function establishUDPRecvSocket()
	local host, port = config.DCS_HOST, config.DCS_PORT
	local ip = socket.dns.toip(host)
	dcsbotgui.UDPRecvSocket = socket.udp()
	dcsbotgui.UDPRecvSocket:setsockname(ip, port)
	dcsbotgui.UDPRecvSocket:settimeout(0.0001)
	dcsbotgui.UDPRecvSocket:setoption('reuseaddr', true)
end

function dcsbotgui.onSimulationFrame()
	-- general idea from HypeMan
	if not dcsbotgui.UDPRecvSocket then
		establishUDPRecvSocket()
	end

	local msg, err
	repeat
		msg, err = dcsbotgui.UDPRecvSocket:receive()
		if err == nil then
			local decoded = JSON:decode(msg)
			if dcsbot[decoded.command] ~= nil then
				dcsbot[decoded.command](decoded)
			end
		end
	until err
end

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