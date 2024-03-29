---
title: Sample Files
parent: Configuration
nav_order: 1
---

# Configuration Sample Files
In the subfolder `samples`, you'll find some configuration-file samples for the bot and the different available plugins.

## dcsserverbot.ini
This sample can be used as a starting point, when you create your own dcsserverbot.ini file.
It is a basic configuration for a dedicated server setup with two dedicated servers being configured to be used with
the bot. The first instance is the default instance, the 2nd instance is named "instance2". This is what you provide
with -w to the dcs.exe process or how your Saved Games folder is named. 

## admin.json
Default file to support the .download command. Here you can configure which files / patterns you want to support for
DCS Admin users to download from your server. You see a lot of examples in there already. If you don't want people to
download specific items, just remove the respective line from your admin.json.

## cloud.json
This file contains the default settings for the DCSServerBot cloud service. You can add your TOKEN in here, that you
received from me.

## commands.json
This shows two examples of custom commands you can create with the commands plugin. One command starts a DCS server
and the 2nd command runs a `dir` command on your server and returns the output. 

## creditsystem.json
Sample file to show you the usage of the credit system. You can get credits for kills or for proper landings (see
greenieboard.json). People can have initial or max points based on their roles, and you can give people specific
discord roles for flight times or credit achievements on your servers. Roles are campaign-based, if you have a 
campaign configured.

## dbexporter.json
Simple DB-Exporter sample, that exports two tables only (missions and statistics) on a regular basis (every hour).
This plugin can be used to export data to another solution like a website, displaying achievement, etc., if that
should not have access to the database directly for whatever reasons (being remote for instance).

## funkman.json
Sample and minimal configuration for the FunkMan plugin. You need at least to point to the place where FunkMan is
installed. The rest of the parameters will be read from your existing funkman.ini or can be filled in manually.

## greenieboard.json
This sample contains a proposal for a points system for carrier landings and shows a possibility to configure a 
persistent embed. In addition, you can see how to configure the Moose.AIRBOSS integration.

## motd.json
This sample contains a default section, that is being used for every server, if nothing else is provided and a specific
section for server "DCS.release_server", that is overwriting the default.

## music.json
Sample configuration which defines a Music upload directory "Music" in your Saved Games\<instance>\ folders. Besides 
that, it defines an SRS sink to be used on a specific SRS channel.

## ovgme.json
With the OvGME plugin you can install OvGME like packages automatically into your DCS servers. The sample shows two
possible ways, by either providing a strict version (2.0.1) or by using the term "latest", to get the latest available
version that is provided in one of the installation directories.

## punishment.json
Again, this sample shows a default setting and two servers. "DCS.release_server" and "instance2", that don't punish
people that are Discord members (@everyone). This might be useful, if you are running public servers but don't want
your Discord members to be banned, kicked or whatnot.

## scheduler.json
The scheduler is a very powerful and thus complex plugin. I tried to pack in as much information that was possible into
the sample, but you might want to look into the [plugin documentation] as well.

### Default-Section
Contains the "warn schedule", meaning at which amount of seconds before a restart / shutdown happens, the users should 
get warned. And a list of weather presets, that can be applied to your missions. Both are optional and need only to be
in your configuration, if you want to warn users or if you want to change the weather on demand.

### DCS.release_server
This sample shows the configuration for the first server. It will run 24/7 but only on threads 2 and 3 (aka core 1).

### mission
This is an example for a mission-only server, where missions start on Sunday at 1800 local time. The server will be 
stopped again automatically on Sunday 24:00 / Monday 00:00 if not stopped manually before.

### instance2
This server will run every day 00:00h to 12:00h. It will rotate its missions every 4 hours, even if the server
is populated (people flying on it). From 00:00 to 08:00 the "Winter Nighttime" preset will be used, between
08:00 and 12:00, the "Winter Daytime" preset.
Two external lua files will be loaded on mission start and on mission end. When the server shuts down, the whole PC 
will reboot with the onShutdown parameter (bot needs to run with Admin rights for such a case).

### instance3
This server runs every day from 12:00 until 24:00. The mission and DCS server restarts after 8hrs mission time 
(480 mins), but only if nobody is flying on the server (populated = false). Whenever the mission restarts, a random
preset will be picked out of the provided list ("Winter Daytime", "Summer Daytime").

## slotblocking.json
Another powerful plugin is the Slotblocking. The sample shows a default configuration, which is valid for every server.
In our case, we restrict the Combined Arms slots to people that are members of your Discord and that carry the Donators
role.

The example for "DCS.release_server" shows the point-based slotblocking system. People can earn points when killing 
specific targets (see list). On the other hand, slots can be blocked until a specific amount of points has been reached
by that user ("points"). The "costs" determine, what happens to the users points when he uses this plane.
There is deposit-like system included, that reserves points when you use a plane and returns them to the user, whenever 
they bring back the plane intact (landing). Another takeoff will create another deposit. If they crash or get killed, 
the deposit is gone, and they'll finally lose their points. This can be enabled with "use_reservations": true like in 
the example.

[plugin documentation]: ../plugins/scheduler.md
