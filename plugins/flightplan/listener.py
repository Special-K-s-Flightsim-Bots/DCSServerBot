import asyncio
import discord
import json
import logging

from core import EventListener, Server, Player, event, chat_command, get_translation
from datetime import datetime, timedelta, timezone
from psycopg.rows import dict_row
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .commands import FlightPlan

_ = get_translation(__name__.split('.')[1])
log = logging.getLogger(__name__)


class FlightPlanEventListener(EventListener["FlightPlan"]):
    """Event listener for flight plan plugin - handles events and chat commands."""

    # ==================== HELPER METHODS ====================

    async def create_flight_plan_markers(self, server: Server, fp: dict, timeout: int = 0) -> bool:
        """
        Create F10 map markers for a flight plan.

        Parameters
        ----------
        server : Server
            The DCS server to create markers on
        fp : dict
            Flight plan data from database
        timeout : int
            Seconds until markers auto-remove (0 = permanent)

        Returns
        -------
        bool
            True if markers were created successfully
        """
        try:
            # Get positions from stored JSONB
            dep_pos = fp.get('departure_position')
            if isinstance(dep_pos, str):
                dep_pos = json.loads(dep_pos)
            dest_pos = fp.get('destination_position')
            if isinstance(dest_pos, str):
                dest_pos = json.loads(dest_pos)

            if not dep_pos or not dest_pos:
                self.log.warning(f"Flight plan {fp['id']} missing position data")
                return False

            # Alternate position
            alt_pos = fp.get('alternate_position')
            if isinstance(alt_pos, str):
                alt_pos = json.loads(alt_pos)

            # Waypoints
            waypoints = fp.get('waypoints') or []
            if isinstance(waypoints, str):
                waypoints = json.loads(waypoints)

            # ETD formatting
            etd_str = ""
            if fp.get('etd'):
                etd = fp['etd']
                if isinstance(etd, str):
                    etd_str = etd
                else:
                    etd_str = etd.strftime('%H:%M UTC')

            # Build command data
            data = {
                "command": "createFlightPlanMarkers",
                "plan_id": fp['id'],
                "coalition": fp.get('coalition', 0),
                "callsign": fp.get('callsign', 'Unknown'),
                "departure_name": fp.get('departure', 'Unknown'),
                "departure_x": dep_pos.get('x', 0),
                "departure_z": dep_pos.get('z', 0),
                "destination_name": fp.get('destination', 'Unknown'),
                "destination_x": dest_pos.get('x', 0),
                "destination_z": dest_pos.get('z', 0),
                "alternate_name": fp.get('alternate', ''),
                "alternate_x": alt_pos.get('x') if alt_pos else None,
                "alternate_z": alt_pos.get('z') if alt_pos else None,
                "aircraft_type": fp.get('aircraft_type', ''),
                "cruise_altitude": fp.get('cruise_altitude', 0),
                "etd": etd_str,
                "waypoints": json.dumps(waypoints),
                "timeout": timeout
            }

            await server.send_to_dcs(data)
            return True

        except Exception as e:
            self.log.error(f"Error creating flight plan markers: {e}")
            return False

    async def remove_flight_plan_markers(self, server: Server, plan_id: int) -> bool:
        """Remove F10 map markers for a flight plan."""
        try:
            data = {
                "command": "removeFlightPlanMarkers",
                "plan_id": plan_id
            }
            await server.send_to_dcs(data)

            # Clean up marker records from database
            async with self.apool.connection() as conn:
                await conn.execute(
                    "DELETE FROM flightplan_markers WHERE flight_plan_id = %s",
                    (plan_id,)
                )

            return True
        except Exception as e:
            self.log.error(f"Error removing flight plan markers: {e}")
            return False

    async def publish_flight_plan(self, fp: dict, status: str) -> Optional[int]:
        """
        Publish or update a flight plan embed in Discord.

        Returns the message ID if successful.
        """
        config = self.plugin.get_config()
        channel_id = config.get('publish_channel')

        if not channel_id:
            return None

        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                self.log.warning(f"Publish channel {channel_id} not found")
                return None

            # Get pilot name
            async with self.apool.connection() as conn:
                cursor = await conn.execute(
                    "SELECT name FROM players WHERE ucid = %s",
                    (fp['player_ucid'],)
                )
                row = await cursor.fetchone()
                pilot_name = row[0] if row else 'Unknown'

            # Create embed
            status_colors = {
                'filed': discord.Color.blue(),
                'active': discord.Color.green(),
                'activated': discord.Color.green(),
                'completed': discord.Color.dark_green(),
                'cancelled': discord.Color.red()
            }

            embed = discord.Embed(
                title=_('Flight Plan #{} - {}').format(fp['id'], fp.get('callsign', 'N/A')),
                color=status_colors.get(status, discord.Color.blue())
            )

            embed.add_field(name=_('Status'), value=status.upper(), inline=True)
            embed.add_field(name=_('Pilot'), value=pilot_name, inline=True)
            if fp.get('aircraft_type'):
                embed.add_field(name=_('Aircraft'), value=fp['aircraft_type'], inline=True)

            route_str = f"{fp.get('departure', '?')} → {fp.get('destination', '?')}"
            embed.add_field(name=_('Route'), value=route_str, inline=True)

            if fp.get('alternate'):
                embed.add_field(name=_('Alternate'), value=fp['alternate'], inline=True)

            if fp.get('cruise_altitude'):
                fl = fp['cruise_altitude'] // 100
                embed.add_field(name=_('Cruise'), value=f"FL{fl:03d}", inline=True)

            if fp.get('etd'):
                etd = fp['etd']
                if isinstance(etd, datetime):
                    etd_str = etd.strftime('%H:%M UTC')
                else:
                    etd_str = str(etd)
                embed.add_field(name=_('ETD'), value=etd_str, inline=True)

            # Waypoints
            waypoints = fp.get('waypoints')
            if waypoints:
                if isinstance(waypoints, str):
                    waypoints = json.loads(waypoints)
                if waypoints:
                    wp_names = [wp.get('name', '?') for wp in waypoints[:5]]
                    wp_str = ' → '.join(wp_names)
                    if len(waypoints) > 5:
                        wp_str += f" (+{len(waypoints) - 5})"
                    embed.add_field(name=_('Waypoints'), value=wp_str, inline=False)

            if fp.get('remarks'):
                embed.add_field(name=_('Remarks'), value=fp['remarks'][:200], inline=False)

            if fp.get('server_name'):
                embed.set_footer(text=f"Server: {fp['server_name']}")

            # Check if we should update an existing message
            existing_msg_id = fp.get('discord_message_id')
            if existing_msg_id:
                try:
                    msg = await channel.fetch_message(int(existing_msg_id))
                    await msg.edit(embed=embed)
                    return existing_msg_id
                except discord.NotFound:
                    pass

            # Send new message
            msg = await channel.send(embed=embed)

            # Store message ID
            async with self.apool.connection() as conn:
                await conn.execute(
                    "UPDATE flightplan_plans SET discord_message_id = %s WHERE id = %s",
                    (msg.id, fp['id'])
                )

            return msg.id

        except Exception as e:
            self.log.error(f"Error publishing flight plan: {e}")
            return None

    async def cancel_stale_plans(self, server: Server) -> int:
        """Cancel stale flight plans for a server."""
        now = datetime.now(timezone.utc)

        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                UPDATE flightplan_plans
                SET status = 'cancelled'
                WHERE server_name = %s
                AND status IN ('filed', 'active')
                AND stale_at < %s
                RETURNING id
            """, (server.name, now))
            cancelled = await cursor.fetchall()

        # Remove markers for cancelled plans
        for (plan_id,) in cancelled:
            await self.remove_flight_plan_markers(server, plan_id)

        if cancelled:
            self.log.info(f"Cancelled {len(cancelled)} stale flight plans for {server.name}")

        return len(cancelled)

    async def recreate_active_markers(self, server: Server) -> int:
        """Recreate F10 markers for all active flight plans on server start."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM flightplan_plans
                    WHERE server_name = %s AND status = 'active'
                """, (server.name,))
                active_plans = await cursor.fetchall()

        count = 0
        for fp in active_plans:
            if await self.create_flight_plan_markers(server, fp, timeout=0):
                count += 1

        if count:
            self.log.info(f"Recreated markers for {count} active flight plans on {server.name}")

        return count

    # ==================== EVENT HANDLERS ====================

    @event(name="onSimulationStart")
    async def on_simulation_start(self, server: Server, _data: dict) -> None:
        """Handle mission start - cancel stale plans and recreate active markers."""
        config = self.get_config(server)

        # Cancel stale plans if configured
        if config.get('auto_cancel_stale', True):
            await self.cancel_stale_plans(server)

        # Recreate markers for active plans
        await self.recreate_active_markers(server)

    @event(name="createFlightPlanMarkers")
    async def on_create_markers(self, server: Server, data: dict) -> None:
        """Handle marker creation confirmation from Lua."""
        plan_id = data.get('plan_id')
        marker_ids = data.get('marker_ids', [])

        if not plan_id or not marker_ids:
            return

        # Store marker IDs in database
        async with self.apool.connection() as conn:
            for marker in marker_ids:
                await conn.execute("""
                    INSERT INTO flightplan_markers
                    (server_name, flight_plan_id, marker_id, marker_type)
                    VALUES (%s, %s, %s, %s)
                """, (server.name, plan_id, marker['id'], marker['type']))

    @event(name="removeFlightPlanMarkers")
    async def on_remove_markers(self, server: Server, data: dict) -> None:
        """Handle marker removal confirmation from Lua."""
        plan_id = data.get('plan_id')
        if plan_id:
            self.log.debug(f"Removed {data.get('removed_count', 0)} markers for plan {plan_id}")

    @event(name="onPlayerChangeSlot")
    async def on_player_change_slot(self, server: Server, data: dict) -> None:
        """Create F10 menu when player takes a slot."""
        # Only handle when player takes a coalition slot (has 'side' in data)
        if 'side' not in data or data.get('side') == 0:
            return

        player = server.get_player(ucid=data.get('ucid'), active=True)
        if not player:
            return

        # Create F10 menu for the player
        await self._create_flightplan_menu(server, player)

    @event(name="flightplan")
    async def on_flightplan_callback(self, server: Server, data: dict) -> None:
        """Handle F10 menu callbacks for flight plans."""
        player_id = data.get('from')
        player = server.get_player(id=player_id)
        if not player:
            return

        params = data.get('params', {})
        action = params.get('action')

        if action == 'view_plans':
            await self._menu_view_plans(server, player)
        elif action == 'my_plan':
            await self._menu_my_plan(server, player)
        elif action == 'plot_all':
            await self._menu_plot_all(server, player)
        elif action == 'plot_plan':
            plan_id = params.get('plan_id')
            if plan_id:
                await self._menu_plot_plan(server, player, plan_id)
        elif action == 'activate':
            plan_id = params.get('plan_id')
            await self._menu_activate(server, player, plan_id)
        elif action == 'complete':
            await self._menu_complete(server, player)
        elif action == 'cancel':
            await self._menu_cancel(server, player)

    # ==================== CHAT COMMANDS ====================

    @chat_command(name="flightplan", aliases=["fp"], help=_("Show your active flight plan"))
    async def cmd_flightplan(self, server: Server, player: Player, params: list[str]) -> None:
        """Show the player's active flight plan."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM flightplan_plans
                    WHERE player_ucid = %s AND status IN ('filed', 'active')
                    ORDER BY filed_at DESC LIMIT 1
                """, (player.ucid,))
                fp = await cursor.fetchone()

        if not fp:
            await player.sendChatMessage(_("You have no active flight plan."))
            return

        status = fp['status'].upper()
        route = f"{fp.get('departure', '?')} -> {fp.get('destination', '?')}"

        msg = f"Flight Plan #{fp['id']} ({status}): {fp.get('callsign', 'N/A')} | {route}"

        if fp.get('cruise_altitude'):
            msg += f" | FL{fp['cruise_altitude'] // 100:03d}"

        if fp.get('etd'):
            etd = fp['etd']
            if isinstance(etd, datetime):
                msg += f" | ETD {etd.strftime('%H:%M')}"

        await player.sendChatMessage(msg)

    @chat_command(name="plotfp", help=_("Plot flight plan on F10 map for 30 seconds"))
    async def cmd_plotfp(self, server: Server, player: Player, params: list[str]) -> None:
        """Plot a flight plan on the F10 map temporarily."""
        plan_id = None

        if params:
            try:
                plan_id = int(params[0])
            except ValueError:
                await player.sendChatMessage(_("Usage: -plotfp [plan_id]"))
                return

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                if plan_id:
                    await cursor.execute(
                        "SELECT * FROM flightplan_plans WHERE id = %s",
                        (plan_id,)
                    )
                else:
                    # Get player's most recent active plan
                    await cursor.execute("""
                        SELECT * FROM flightplan_plans
                        WHERE player_ucid = %s AND status IN ('filed', 'active')
                        ORDER BY filed_at DESC LIMIT 1
                    """, (player.ucid,))

                fp = await cursor.fetchone()

        if not fp:
            await player.sendChatMessage(_("Flight plan not found."))
            return

        # Get config for timeout
        config = self.get_config(server)
        timeout = config.get('marker_timeout', 30)

        if await self.create_flight_plan_markers(server, fp, timeout=timeout):
            await player.sendChatMessage(
                _("Flight plan #{} plotted on F10 map for {} seconds.").format(fp['id'], timeout)
            )
        else:
            await player.sendChatMessage(_("Could not plot flight plan. Missing position data."))

    @chat_command(name="fileplan", help=_("Quick file a flight plan: -fileplan <DEP> <DEST> [aircraft]"))
    async def cmd_fileplan(self, server: Server, player: Player, params: list[str]) -> None:
        """Quick file a flight plan from in-game."""
        if len(params) < 2:
            await player.sendChatMessage(_("Usage: -fileplan <departure> <destination> [aircraft]"))
            return

        departure = params[0]
        destination = params[1]
        aircraft = params[2] if len(params) > 2 else (player.unit_type or 'Unknown')
        callsign = player.name

        # Get theater from server
        theater = server.current_mission.map if server.current_mission else None

        async with self.apool.connection() as conn:
            # Parse departure and destination
            from .utils import parse_waypoint_input, WaypointType

            dep_wp = await parse_waypoint_input(departure, server, conn, theater)
            dest_wp = await parse_waypoint_input(destination, server, conn, theater)

            dep_position = dep_wp.to_dict() if dep_wp.waypoint_type != WaypointType.UNKNOWN else None
            dest_position = dest_wp.to_dict() if dest_wp.waypoint_type != WaypointType.UNKNOWN else None

            # Calculate stale time
            config = self.get_config(server)
            stale_hours = config.get('stale_hours', 24)
            filed_at = datetime.now(timezone.utc)
            stale_at = filed_at + timedelta(hours=stale_hours)

            cursor = await conn.execute("""
                INSERT INTO flightplan_plans
                (player_ucid, server_name, callsign, aircraft_type, departure, destination,
                 departure_position, destination_position, stale_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                player.ucid, server.name, callsign, aircraft,
                dep_wp.name, dest_wp.name,
                json.dumps(dep_position) if dep_position else None,
                json.dumps(dest_position) if dest_position else None,
                stale_at
            ))
            result = await cursor.fetchone()
            plan_id = result[0]

        await player.sendChatMessage(
            _("Flight plan #{} filed: {} -> {} ({})").format(
                plan_id, dep_wp.name, dest_wp.name, aircraft
            )
        )

    @chat_command(name="activatefp", help=_("Activate your filed flight plan"))
    async def cmd_activatefp(self, server: Server, player: Player, params: list[str]) -> None:
        """Activate a filed flight plan."""
        plan_id = None

        if params:
            try:
                plan_id = int(params[0])
            except ValueError:
                await player.sendChatMessage(_("Usage: -activatefp [plan_id]"))
                return

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                if plan_id:
                    await cursor.execute(
                        "SELECT * FROM flightplan_plans WHERE id = %s AND player_ucid = %s",
                        (plan_id, player.ucid)
                    )
                else:
                    await cursor.execute("""
                        SELECT * FROM flightplan_plans
                        WHERE player_ucid = %s AND status = 'filed'
                        ORDER BY filed_at DESC LIMIT 1
                    """, (player.ucid,))

                fp = await cursor.fetchone()

                if not fp:
                    await player.sendChatMessage(_("No filed flight plan found."))
                    return

                if fp['status'] != 'filed':
                    await player.sendChatMessage(
                        _("Flight plan #{} is already {}.").format(fp['id'], fp['status'])
                    )
                    return

                now = datetime.now(timezone.utc)
                await conn.execute(
                    "UPDATE flightplan_plans SET status = 'active', activated_at = %s WHERE id = %s",
                    (now, fp['id'])
                )

        # Create markers
        await self.create_flight_plan_markers(server, fp, timeout=0)

        # Publish to Discord
        config = self.get_config(server)
        if config.get('publish_on_activate', True):
            await self.publish_flight_plan(fp, 'activated')

        await player.sendChatMessage(_("Flight plan #{} activated.").format(fp['id']))

    @chat_command(name="completefp", help=_("Complete your active flight plan"))
    async def cmd_completefp(self, server: Server, player: Player, params: list[str]) -> None:
        """Complete an active flight plan."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM flightplan_plans
                    WHERE player_ucid = %s AND status = 'active'
                    ORDER BY activated_at DESC LIMIT 1
                """, (player.ucid,))
                fp = await cursor.fetchone()

                if not fp:
                    await player.sendChatMessage(_("No active flight plan found."))
                    return

                now = datetime.now(timezone.utc)
                await conn.execute(
                    "UPDATE flightplan_plans SET status = 'completed', completed_at = %s WHERE id = %s",
                    (now, fp['id'])
                )

        # Remove markers
        await self.remove_flight_plan_markers(server, fp['id'])

        # Update Discord
        await self.publish_flight_plan(fp, 'completed')

        await player.sendChatMessage(_("Flight plan #{} completed.").format(fp['id']))

    @chat_command(name="cancelfp", help=_("Cancel your flight plan"))
    async def cmd_cancelfp(self, server: Server, player: Player, params: list[str]) -> None:
        """Cancel a flight plan."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM flightplan_plans
                    WHERE player_ucid = %s AND status IN ('filed', 'active')
                    ORDER BY filed_at DESC LIMIT 1
                """, (player.ucid,))
                fp = await cursor.fetchone()

                if not fp:
                    await player.sendChatMessage(_("No active flight plan found."))
                    return

                await conn.execute(
                    "UPDATE flightplan_plans SET status = 'cancelled' WHERE id = %s",
                    (fp['id'],)
                )

        # Remove markers
        await self.remove_flight_plan_markers(server, fp['id'])

        # Update Discord
        await self.publish_flight_plan(fp, 'cancelled')

        await player.sendChatMessage(_("Flight plan #{} cancelled.").format(fp['id']))

    # ==================== F10 MENU METHODS ====================

    async def _get_visible_plans(self, server_name: str, coalition: int = None) -> list[dict]:
        """Get flight plans visible to a coalition (filed or active)."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                if coalition:
                    await cursor.execute("""
                        SELECT fp.*, p.name as pilot_name
                        FROM flightplan_plans fp
                        LEFT JOIN players p ON fp.player_ucid = p.ucid
                        WHERE fp.server_name = %s
                        AND fp.status IN ('filed', 'active')
                        AND (fp.coalition = %s OR fp.coalition = 0)
                        ORDER BY fp.filed_at DESC
                        LIMIT 10
                    """, (server_name, coalition))
                else:
                    await cursor.execute("""
                        SELECT fp.*, p.name as pilot_name
                        FROM flightplan_plans fp
                        LEFT JOIN players p ON fp.player_ucid = p.ucid
                        WHERE fp.server_name = %s
                        AND fp.status IN ('filed', 'active')
                        ORDER BY fp.filed_at DESC
                        LIMIT 10
                    """, (server_name,))
                return await cursor.fetchall()

    async def _create_flightplan_menu(self, server: Server, player: Player):
        """Create F10 menu for flight plan operations."""
        # Get player's plans and visible plans
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Player's own plans
                await cursor.execute("""
                    SELECT * FROM flightplan_plans
                    WHERE player_ucid = %s AND server_name = %s
                    AND status IN ('filed', 'active')
                    ORDER BY filed_at DESC LIMIT 5
                """, (player.ucid, server.name))
                my_plans = await cursor.fetchall()

        # Get all visible plans for plotting
        visible_plans = await self._get_visible_plans(server.name, player.side.value if player.side else None)

        # Build menu structure
        menu = [{
            "Flight Plan": [
                {
                    "View Active Plans": {
                        "command": "flightplan",
                        "params": {"action": "view_plans"}
                    }
                },
                {
                    "My Flight Plan": {
                        "command": "flightplan",
                        "params": {"action": "my_plan"}
                    }
                }
            ]
        }]

        # Add plot options
        if visible_plans:
            # Plot All option
            menu[0]["Flight Plan"].append({
                "Plot All Plans (30s)": {
                    "command": "flightplan",
                    "params": {"action": "plot_all"}
                }
            })

            # Plot by ID submenu
            plot_menu = []
            for plan in visible_plans[:5]:
                callsign = plan.get('callsign', 'Unknown')
                route = f"{plan.get('departure', '?')} -> {plan.get('destination', '?')}"
                label = f"#{plan['id']}: {callsign[:15]}"
                plot_menu.append({
                    label: {
                        "command": "flightplan",
                        "params": {"action": "plot_plan", "plan_id": plan['id']}
                    }
                })
            if plot_menu:
                menu[0]["Flight Plan"].append({"Plot Plan": plot_menu})

        # Add activate option for player's filed plans
        filed_plans = [p for p in my_plans if p['status'] == 'filed']
        if filed_plans:
            activate_menu = []
            for plan in filed_plans[:5]:
                route = f"{plan.get('departure', '?')} -> {plan.get('destination', '?')}"
                label = f"#{plan['id']}: {route[:25]}"
                activate_menu.append({
                    label: {
                        "command": "flightplan",
                        "params": {"action": "activate", "plan_id": plan['id']}
                    }
                })
            if activate_menu:
                menu[0]["Flight Plan"].append({"Activate Plan": activate_menu})

        # Add complete/cancel options if player has active plan
        active_plans = [p for p in my_plans if p['status'] == 'active']
        if active_plans:
            menu[0]["Flight Plan"].append({
                "Complete Flight": {
                    "command": "flightplan",
                    "params": {"action": "complete"}
                }
            })
            menu[0]["Flight Plan"].append({
                "Cancel Flight": {
                    "command": "flightplan",
                    "params": {"action": "cancel"}
                }
            })

        # Send menu to DCS
        group_id = player.group_id
        if group_id:
            await server.send_to_dcs({
                "command": "createMenu",
                "playerID": player.id,
                "groupID": group_id,
                "menu": menu
            })

    async def _menu_view_plans(self, server: Server, player: Player):
        """Handle 'View Active Plans' menu option."""
        plans = await self._get_visible_plans(server.name, player.side.value if player.side else None)

        if not plans:
            await player.sendPopupMessage(_("No active flight plans."), 10)
            return

        msg = "ACTIVE FLIGHT PLANS\n\n"
        for plan in plans[:5]:
            status_marker = "[ACTIVE] " if plan['status'] == 'active' else ""
            callsign = plan.get('callsign', 'Unknown')
            pilot = plan.get('pilot_name', 'Unknown')
            route = f"{plan.get('departure', '?')} -> {plan.get('destination', '?')}"
            msg += f"{status_marker}#{plan['id']}: {callsign}\n"
            msg += f"    Pilot: {pilot}\n"
            msg += f"    Route: {route}\n"
            if plan.get('cruise_altitude'):
                msg += f"    FL{plan['cruise_altitude'] // 100:03d}\n"
            msg += "\n"

        if len(plans) > 5:
            msg += f"... and {len(plans) - 5} more plans"

        await player.sendPopupMessage(msg, 20)

    async def _menu_my_plan(self, server: Server, player: Player):
        """Handle 'My Flight Plan' menu option."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM flightplan_plans
                    WHERE player_ucid = %s AND status IN ('filed', 'active')
                    ORDER BY filed_at DESC LIMIT 1
                """, (player.ucid,))
                fp = await cursor.fetchone()

        if not fp:
            await player.sendPopupMessage(_("You have no active flight plan."), 10)
            return

        status = fp['status'].upper()
        route = f"{fp.get('departure', '?')} -> {fp.get('destination', '?')}"

        msg = f"YOUR FLIGHT PLAN\n\n"
        msg += f"Plan #{fp['id']} ({status})\n"
        msg += f"Callsign: {fp.get('callsign', 'N/A')}\n"
        if fp.get('aircraft_type'):
            msg += f"Aircraft: {fp['aircraft_type']}\n"
        msg += f"Route: {route}\n"

        if fp.get('alternate'):
            msg += f"Alternate: {fp['alternate']}\n"

        if fp.get('cruise_altitude'):
            msg += f"Cruise: FL{fp['cruise_altitude'] // 100:03d}\n"

        if fp.get('etd'):
            etd = fp['etd']
            if isinstance(etd, datetime):
                msg += f"ETD: {etd.strftime('%H:%M UTC')}\n"

        if fp.get('waypoints'):
            waypoints = fp['waypoints']
            if isinstance(waypoints, str):
                waypoints = json.loads(waypoints)
            if waypoints:
                wp_names = [wp.get('name', '?') for wp in waypoints[:5]]
                msg += f"Via: {' -> '.join(wp_names)}\n"

        await player.sendPopupMessage(msg, 20)

    async def _menu_plot_all(self, server: Server, player: Player):
        """Handle 'Plot All Plans' menu option."""
        plans = await self._get_visible_plans(server.name, player.side.value if player.side else None)

        if not plans:
            await player.sendPopupMessage(_("No flight plans to plot."), 10)
            return

        config = self.get_config(server)
        timeout = config.get('marker_timeout', 30)

        plotted = 0
        plan_ids = []
        for plan in plans:
            if await self.create_flight_plan_markers(server, plan, timeout=timeout):
                plotted += 1
                plan_ids.append(plan['id'])

        await player.sendPopupMessage(
            _("Plotted {} flight plan(s) on F10 map.\nMarkers will disappear in {} seconds.").format(plotted, timeout),
            10
        )

    async def _menu_plot_plan(self, server: Server, player: Player, plan_id: int):
        """Handle 'Plot Plan' menu option for a specific plan."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT * FROM flightplan_plans WHERE id = %s",
                    (plan_id,)
                )
                fp = await cursor.fetchone()

        if not fp:
            await player.sendPopupMessage(_("Flight plan not found."), 10)
            return

        config = self.get_config(server)
        timeout = config.get('marker_timeout', 30)

        if await self.create_flight_plan_markers(server, fp, timeout=timeout):
            await player.sendPopupMessage(
                _("Flight plan #{} plotted on F10 map for {} seconds.").format(plan_id, timeout),
                10
            )
        else:
            await player.sendPopupMessage(_("Could not plot flight plan. Missing position data."), 10)

    async def _menu_activate(self, server: Server, player: Player, plan_id: int = None):
        """Handle 'Activate Plan' menu option."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                if plan_id:
                    await cursor.execute(
                        "SELECT * FROM flightplan_plans WHERE id = %s AND player_ucid = %s",
                        (plan_id, player.ucid)
                    )
                else:
                    await cursor.execute("""
                        SELECT * FROM flightplan_plans
                        WHERE player_ucid = %s AND status = 'filed'
                        ORDER BY filed_at DESC LIMIT 1
                    """, (player.ucid,))

                fp = await cursor.fetchone()

                if not fp:
                    await player.sendPopupMessage(_("No filed flight plan found."), 10)
                    return

                if fp['status'] != 'filed':
                    await player.sendPopupMessage(
                        _("Flight plan #{} is already {}.").format(fp['id'], fp['status']),
                        10
                    )
                    return

                now = datetime.now(timezone.utc)
                await conn.execute(
                    "UPDATE flightplan_plans SET status = 'active', activated_at = %s WHERE id = %s",
                    (now, fp['id'])
                )

        # Create markers
        await self.create_flight_plan_markers(server, fp, timeout=0)

        # Publish to Discord
        config = self.get_config(server)
        if config.get('publish_on_activate', True):
            await self.publish_flight_plan(fp, 'activated')

        await player.sendPopupMessage(
            _("FLIGHT PLAN ACTIVATED\n\nPlan #{} is now active.\nRoute plotted on F10 map.").format(fp['id']),
            15
        )

        # Refresh menu
        await self._create_flightplan_menu(server, player)

    async def _menu_complete(self, server: Server, player: Player):
        """Handle 'Complete Flight' menu option."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM flightplan_plans
                    WHERE player_ucid = %s AND status = 'active'
                    ORDER BY activated_at DESC LIMIT 1
                """, (player.ucid,))
                fp = await cursor.fetchone()

                if not fp:
                    await player.sendPopupMessage(_("No active flight plan found."), 10)
                    return

                now = datetime.now(timezone.utc)
                await conn.execute(
                    "UPDATE flightplan_plans SET status = 'completed', completed_at = %s WHERE id = %s",
                    (now, fp['id'])
                )

        # Remove markers
        await self.remove_flight_plan_markers(server, fp['id'])

        # Update Discord
        await self.publish_flight_plan(fp, 'completed')

        await player.sendPopupMessage(
            _("FLIGHT COMPLETE\n\nPlan #{} completed.\nLogged to your record.").format(fp['id']),
            15
        )

        # Refresh menu
        await self._create_flightplan_menu(server, player)

    async def _menu_cancel(self, server: Server, player: Player):
        """Handle 'Cancel Flight' menu option."""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM flightplan_plans
                    WHERE player_ucid = %s AND status IN ('filed', 'active')
                    ORDER BY filed_at DESC LIMIT 1
                """, (player.ucid,))
                fp = await cursor.fetchone()

                if not fp:
                    await player.sendPopupMessage(_("No active flight plan found."), 10)
                    return

                await conn.execute(
                    "UPDATE flightplan_plans SET status = 'cancelled' WHERE id = %s",
                    (fp['id'],)
                )

        # Remove markers
        await self.remove_flight_plan_markers(server, fp['id'])

        # Update Discord
        await self.publish_flight_plan(fp, 'cancelled')

        await player.sendPopupMessage(
            _("FLIGHT CANCELLED\n\nPlan #{} cancelled.").format(fp['id']),
            10
        )

        # Refresh menu
        await self._create_flightplan_menu(server, player)
