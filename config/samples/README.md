# Configuration Samples
In the subfolder `samples`, you'll find some configuration-file samples for the bot and the different available plugins.

## main.yaml / nodes.yaml / servers.yaml
See [README](../../README.md)

## plugins/admin.yaml
Default file to support the `/download` command. Here you can configure which files / patterns you want to support for
DCS Admin users to download from your server. You see a lot of examples in there already. If you don't want people to
download specific items, just remove the respective line from your admin.json.

## plugins/cloud.yaml
This file contains the default settings for the DCSServerBot cloud plugin. You can add your TOKEN in here, that you
received from me, if you participate in the cloud statistics.

## plugins/commands.yaml
This shows two examples of custom commands you can create with the commands plugin. One command starts a DCS server
and the 2nd command runs a `dir` command on your server and returns the output. 

## plugins/creditsystem.yaml
Sample file to show you the usage of the credit system. You can get credits for kills or for proper landings (see
greenieboard.yaml). People can have initial or max points based on their roles, and you can give people specific
discord roles for flight times or credit achievements on your servers. Roles are campaign-based, if you have a 
campaign configured.

## plugins/dbexporter.yaml
Simple DB-Exporter sample, that exports two tables only (missions and statistics) on a regular basis (every hour).
This plugin can be used to export data to another solution like a website, displaying achievement, etc., if that
should not have access to the database directly for whatever reasons (being remote for instance).

## plugins/funkman.yaml
Sample and minimal configuration for the FunkMan plugin. You need at least to point to the place where FunkMan is
installed. The rest of the parameters will be read from your existing funkman.ini or can be filled in manually.

## plugins/greenieboard.yaml
This sample contains a proposal for a points system for carrier landings and shows a possibility to configure a 
persistent embed. In addition, you can see how to configure the Moose.AIRBOSS integration.

## plugins/motd.yaml
This sample contains a default section, that is being used for every server, if nothing else is provided and a specific
section for server "DCS.openbeta_server", that is overwriting the default.

## services/music.yaml
Sample configuration which defines a Music upload directory "Music" in your Saved Games\<instance>\ folders. Besides 
that, it defines an SRS radio to be used on a specific SRS channel.

## services/ovgme.json
With the OvGME plugin you can install OvGME like packages automatically into your DCS servers. The sample shows two
possible ways, by either providing a strict version (2.0.1) or by using the term "latest", to get the latest available
version that is provided in one of the installation directories.

## plugins/punishment.yaml
Again, this sample shows a default setting and two servers. "DCS.openbeta_server" and "instance2", that don't punish
people that are Discord members (@everyone). This might be useful, if you are running public servers but don't want
your Discord members to be banned, kicked or whatnot.

## plugins/restapi.yaml
Default configuration for the RestAPI webservice.

## plugins/scheduler.yaml
This sample shows the configuration of 4 servers. 
* __DCS.openbeta_server__ is just running 24/7. This will be your default.<br>
* __instance2__ only runs in the morning from 0-12hrs. it rotates the mission at 04 AM and 08 AM., even if people are flying
(`populated: true`). onMissionStart and onMissionEnd contain two scripts that should be run, when the mission starts
or ends. And onShutdown contains a command (in this case a restart of the whole PC), when the server is being shut down.<br>
* __instance3__ will run in the afternoon from 12 to 24hrs. It will restart with a DCS server shutdown after 480 mins (8 hrs) 
mission time, but only, if no player is online (`populated: false`).<br>
* __mission__ is a possible configuration for a mission server. It starts every Sunday at 18:00 LT and runs until midnight.

### Default-Section
Contains the "warn schedule", meaning at which amount of seconds before a restart / shutdown happens, the users should 
get warned. This is optional and needs only to be in your configuration, if you want to warn your users (recommended).

## plugins/slotblocking.yaml
Another powerful plugin is the Slotblocking. The sample shows a default configuration, which is valid for every server.
In our case, we restrict the Combined Arms slots to people that are members of your Discord and that carry the Donators
role.
The example for "DCS.openbeta_server" shows the point-based slotblocking system. People can earn points when killing 
specific targets (see list). On the other hand, slots can be blocked until a specific amount of points has been reached
by that user ("points"). The "costs" determine, what happens to the users points when he uses this plane.
There is deposit-like system included, that reserves points when you use a plane and returns them to the user, whenever 
they bring back the plane intact (landing). Another takeoff will create another deposit. If they crash or get killed, 
the deposit is gone, and they'll finally lose their points. This can be enabled with `use_reservations: true` like in 
the example.
