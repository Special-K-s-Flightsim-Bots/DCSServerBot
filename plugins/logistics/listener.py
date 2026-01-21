import asyncio
import discord
import json
import logging
import re

from core import EventListener, Server, event, chat_command, Player, Side
from datetime import datetime, timezone, timedelta
from discord.ext import tasks
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .commands import Logistics

log = logging.getLogger(__name__)


def _normalize_airbase_name(name: str) -> str:
    """
    Normalize an airbase name for comparison.
    Handles variations like 'Batumi', 'Batumi-Chorokhi', 'Batumi Airbase', etc.
    """
    if not name:
        return ""
    # Convert to lowercase
    name = name.lower().strip()
    # Remove common suffixes/prefixes
    suffixes = [' airbase', ' airport', ' airfield', ' afb', ' ab', ' intl', ' international',
                '-airbase', '-airport', '-airfield', '_airbase', '_airport', '_airfield']
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    # Remove non-alphanumeric characters except spaces and hyphens for further comparison
    return name


def _airbase_names_match(place_name: str, dest_name: str) -> bool:
    """
    Check if two airbase names refer to the same location.
    Uses strict matching to avoid false positives.

    Returns True only if:
    1. Exact match (case-insensitive, normalized)
    2. One name is a clear prefix/base of the other (e.g., "Batumi" matches "Batumi-Chorokhi")
       but only if it's a complete word boundary match
    """
    if not place_name or not dest_name:
        return False

    norm_place = _normalize_airbase_name(place_name)
    norm_dest = _normalize_airbase_name(dest_name)

    # Exact match after normalization
    if norm_place == norm_dest:
        return True

    # Check if one is a word-boundary prefix of the other
    # "Batumi" should match "Batumi-Chorokhi" but not "BatumiX"
    shorter, longer = (norm_place, norm_dest) if len(norm_place) <= len(norm_dest) else (norm_dest, norm_place)

    if longer.startswith(shorter):
        # Check that the character after the shorter name is a word boundary
        if len(longer) == len(shorter):
            return True
        next_char = longer[len(shorter)]
        if next_char in ' -_':
            return True

    return False


class LogisticsEventListener(EventListener["Logistics"]):
    """
    Event listener for logistics plugin.
    Handles in-game chat commands, DCS events, and map marker management.
    """

    def __init__(self, plugin: "Logistics"):
        super().__init__(plugin)
        self._delivery_check_task = None

    async def shutdown(self):
        if self._delivery_check_task:
            self._delivery_check_task.cancel()

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        """Initialize logistics state when server registers."""
        log.debug(f"Logistics: Server {server.name} registered")

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, data: dict) -> None:
        """Recreate markers for assigned tasks and clean up stale tasks when mission starts."""
        log.info(f"Logistics: Mission started on {server.name}")

        # Clean up stale tasks
        await self._cancel_stale_tasks(server)

        # Recreate markers for assigned tasks
        await self._recreate_assigned_task_markers(server)

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        """Handle mission events for delivery detection and F10 menu creation."""
        event_name = data.get('eventName')

        if event_name == 'S_EVENT_BIRTH':
            # Create F10 menu when player spawns (reliable group_id from mission event)
            initiator = data.get('initiator', {})
            player_name = initiator.get('name')
            if not player_name:
                return

            player = server.get_player(name=player_name)
            if not player:
                return

            group_id = initiator.get('group', {}).get('id_')
            if group_id is not None:
                await self._create_logistics_menu(server, player, group_id=group_id)

        elif event_name == 'S_EVENT_LAND':
            await self._check_delivery_on_landing(server, data)

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        """Show player their assigned task when they select a slot."""
        # Only handle when player takes a coalition slot (has 'side' in data)
        if 'side' not in data or data.get('side') == 0:
            return

        player = server.get_player(ucid=data.get('ucid'), active=True)
        if not player:
            return

        # Notify player of their assigned task (menu creation moved to S_EVENT_BIRTH)
        task = await self._get_assigned_task(player.ucid, server.name)
        if task:
            await self._notify_player_of_task(player, task)

    @event(name="createLogisticsMarkers")
    async def onCreateLogisticsMarkers(self, server: Server, data: dict) -> None:
        """Store marker IDs from Lua for cleanup tracking."""
        task_id = data.get('task_id')
        marker_ids = data.get('marker_ids', [])
        log.debug(f"Logistics: Markers created for task {task_id}: {len(marker_ids)} markers")
        await self._store_marker_ids(server.name, task_id, marker_ids)

    @event(name="removeLogisticsMarkers")
    async def onRemoveLogisticsMarkers(self, server: Server, data: dict) -> None:
        """Handle marker removal confirmation from Lua."""
        task_id = data.get('task_id')
        removed_count = data.get('removed_count', 0)
        log.debug(f"Logistics: Removed {removed_count} markers for task {task_id}")

    @event(name="logisticsSimulationStart")
    async def onLogisticsSimulationStart(self, server: Server, data: dict) -> None:
        """Handle mission start - recreate markers for assigned tasks."""
        log.info(f"Logistics: Simulation started on {server.name}, recreating assigned task markers")
        await self._recreate_assigned_task_markers(server)

    @event(name="checkDeliveryProximity")
    async def onCheckDeliveryProximity(self, server: Server, data: dict) -> None:
        """Handle proximity check result from DCS."""
        if not data.get('found') or not data.get('within_threshold'):
            return

        task_id = data.get('task_id')
        # Find the player who owns this task
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT assigned_ucid FROM logistics_tasks WHERE id = %s
            """, (task_id,))
            row = await cursor.fetchone()
            if not row:
                return

            player = server.get_player(ucid=row[0])
            if not player:
                return

            log.info(f"Logistics: Proximity check passed for task {task_id}, completing")
            result = await self._complete_task(server, player, task_id)
            if result['success']:
                await player.sendChatMessage(f"Delivery confirmed! Task #{task_id} completed.")
                await player.sendPopupMessage("DELIVERY COMPLETE\n\nTask logged to your record.", 10)

    @event(name="logistics")
    async def onLogisticsCallback(self, server: Server, data: dict) -> None:
        """Handle F10 menu callbacks for logistics."""

        player_id = data.get('from')
        player = server.get_player(id=player_id)
        if not player:
            return

        params = data.get('params', {})
        action = params.get('action')

        if action == 'view_tasks':
            await self._menu_view_tasks(server, player)
        elif action == 'my_task':
            await self._menu_my_task(server, player)
        elif action == 'accept':
            task_id = params.get('task_id')
            if task_id:
                await self._menu_accept_task(server, player, task_id)
        elif action == 'plot_all':
            await self._menu_plot_all(server, player)
        elif action == 'plot_task':
            task_id = params.get('task_id')
            if task_id:
                await self._menu_plot_task(server, player, task_id)
        elif action == 'deliver':
            await self._menu_deliver(server, player)
        elif action == 'abandon':
            await self._menu_abandon(server, player)
        elif action == 'task_details':
            task_id = params.get('task_id')
            if task_id:
                await self._menu_task_details(server, player, task_id)

    # ==================== CHAT COMMANDS ====================

    @chat_command(name="lhelp", aliases=["logistics"], help="Show logistics commands")
    async def lhelp_cmd(self, server: Server, player: Player, params: list[str]):
        """Show logistics-specific commands."""
        msg = (
            "Logistics Commands:\n"
            f"  {self.prefix}tasks - List available tasks\n"
            f"  {self.prefix}accept <id> - Accept a task\n"
            f"  {self.prefix}mytask - Show your current task\n"
            f"  {self.prefix}taskinfo <id> - View task details\n"
            f"  {self.prefix}deliver - Mark task as delivered\n"
            f"  {self.prefix}abandon - Release your task\n"
            f"  {self.prefix}plot <all|id> - Plot tasks on F10 map"
        )
        await player.sendChatMessage(msg)

    @chat_command(name="tasks", help="List available logistics tasks")
    async def tasks_cmd(self, server: Server, player: Player, params: list[str]):
        """Show available tasks for player's coalition."""
        tasks = await self._get_available_tasks(server.name, player.side.value)
        if not tasks:
            await player.sendChatMessage("No logistics tasks available.")
            return

        msg = "Available Logistics Tasks:\n"
        for task in tasks[:5]:  # Limit to 5 in chat
            priority_marker = "!" if task['priority'] == 'urgent' else ""
            msg += f"  #{task['id']}{priority_marker}: {task['cargo_type']} -> {task['destination_name']}\n"

        if len(tasks) > 5:
            msg += f"  ... and {len(tasks) - 5} more. Use -taskinfo <id> for details."

        await player.sendChatMessage(msg)

    @chat_command(name="accept", usage="<task_id>", help="Accept a logistics task")
    async def accept_cmd(self, server: Server, player: Player, params: list[str]):
        """Accept/claim a logistics task."""
        if not params:
            await player.sendChatMessage("Usage: -accept <task_id>")
            return

        try:
            task_id = int(params[0])
        except ValueError:
            await player.sendChatMessage("Invalid task ID. Use -tasks to see available tasks.")
            return

        result = await self._assign_task(server, player, task_id)
        if result['success']:
            await player.sendChatMessage(f"Task #{task_id} accepted! Pickup at {result['source']}.")
            await player.sendPopupMessage(
                f"LOGISTICS TASK ASSIGNED\n\n"
                f"Cargo: {result['cargo']}\n"
                f"Pickup: {result['source']}\n"
                f"Deliver to: {result['destination']}\n"
                f"Deadline: {result['deadline'] or 'None'}",
                20
            )
        else:
            await player.sendChatMessage(f"Cannot accept task: {result['error']}")

    @chat_command(name="mytask", help="Show your current logistics task")
    async def mytask_cmd(self, server: Server, player: Player, params: list[str]):
        """Show the player's currently assigned task."""
        task = await self._get_assigned_task(player.ucid, server.name)
        if not task:
            await player.sendChatMessage("You have no active logistics task.")
            return

        deadline_str = task['deadline'].strftime('%H:%MZ') if task['deadline'] else "None"
        msg = (
            f"Your Task #{task['id']}:\n"
            f"  Cargo: {task['cargo_type']}\n"
            f"  From: {task['source_name']}\n"
            f"  To: {task['destination_name']}\n"
            f"  Deadline: {deadline_str}\n"
            f"  Status: {task['status']}"
        )
        await player.sendChatMessage(msg)

    @chat_command(name="taskinfo", usage="<task_id>", help="View details of a task")
    async def taskinfo_cmd(self, server: Server, player: Player, params: list[str]):
        """View details of any visible task."""
        if not params:
            await player.sendChatMessage("Usage: -taskinfo <task_id>")
            return

        try:
            task_id = int(params[0])
        except ValueError:
            await player.sendChatMessage("Invalid task ID.")
            return

        task = await self._get_task_by_id(task_id, server.name, player.side.value)
        if not task:
            await player.sendChatMessage(f"Task #{task_id} not found or not visible to your coalition.")
            return

        deadline_str = task['deadline'].strftime('%H:%MZ') if task['deadline'] else "None"
        assigned = task.get('assigned_name') or "Unassigned"
        msg = (
            f"Task #{task['id']} ({task['status']}):\n"
            f"  Cargo: {task['cargo_type']}\n"
            f"  From: {task['source_name']}\n"
            f"  To: {task['destination_name']}\n"
            f"  Priority: {task['priority']}\n"
            f"  Deadline: {deadline_str}\n"
            f"  Assigned: {assigned}"
        )
        await player.sendChatMessage(msg)

    @chat_command(name="deliver", help="Mark current task as delivered")
    async def deliver_cmd(self, server: Server, player: Player, params: list[str]):
        """Manual completion option - for when auto-detect fails."""
        try:
            task = await self._get_assigned_task(player.ucid, server.name)
            if not task:
                await player.sendChatMessage("You have no active logistics task.")
                return

            result = await self._complete_task(server, player, task['id'])
            if result['success']:
                await player.sendChatMessage(f"Task #{task['id']} marked as delivered! Well done.")
                await player.sendPopupMessage("DELIVERY COMPLETE\n\nTask logged to your record.", 10)
            else:
                await player.sendChatMessage(f"Cannot complete task: {result['error']}")
        except Exception as e:
            self.log.exception(f"Error in deliver command for player {player.name}: {e}")
            await player.sendChatMessage("An error occurred processing your delivery. Please try again or contact an admin.")

    @chat_command(name="abandon", help="Abandon your current logistics task")
    async def abandon_cmd(self, server: Server, player: Player, params: list[str]):
        """Release the task back to available pool."""
        try:
            task = await self._get_assigned_task(player.ucid, server.name)
            if not task:
                await player.sendChatMessage("You have no active logistics task.")
                return

            result = await self._abandon_task(server, player, task['id'])
            if result['success']:
                await player.sendChatMessage(f"Task #{task['id']} abandoned. It is now available for others.")
            else:
                await player.sendChatMessage(f"Cannot abandon task: {result['error']}")
        except Exception as e:
            self.log.exception(f"Error in abandon command for player {player.name}: {e}")
            await player.sendChatMessage("An error occurred. Please try again or contact an admin.")

    # -request command removed - use Discord /logistics create instead
    # The interactive flow was too cumbersome for in-game chat

    @chat_command(name="plot", usage="<all|task_id>", help="Plot logistics tasks on the F10 map")
    async def plot_cmd(self, server: Server, player: Player, params: list[str]):
        """Plot tasks on the F10 map. Use 'all' for all tasks or a task ID for a specific one."""
        if not params:
            await player.sendChatMessage("Usage: -plot all  OR  -plot <task_id>")
            return

        arg = params[0].lower()

        if arg == "all":
            tasks = await self._get_available_tasks_with_positions(server.name, player.side.value)
            if not tasks:
                await player.sendChatMessage("No logistics tasks available to plot.")
                return

            task_ids = []
            for task in tasks:
                await self._create_markers_for_task(server, task)
                task_ids.append(task['id'])

            await player.sendChatMessage(f"Plotted {len(task_ids)} task(s) on F10 map for 30 seconds.")

            # Schedule removal after 30 seconds
            asyncio.create_task(self._remove_markers_after_delay(server, task_ids, 30))
        else:
            # Plot specific task by ID
            try:
                task_id = int(arg)
            except ValueError:
                await player.sendChatMessage("Invalid task ID. Use -plot all or -plot <number>")
                return

            task = await self._get_task_with_position(task_id, server.name, player.side.value)
            if not task:
                await player.sendChatMessage(f"Task #{task_id} not found or not visible to your coalition.")
                return

            await self._create_markers_for_task(server, task)
            await player.sendChatMessage(f"Plotted task #{task_id} on F10 map for 30 seconds.")

            # Schedule removal after 30 seconds
            asyncio.create_task(self._remove_markers_after_delay(server, [task_id], 30))

    # ==================== HELPER METHODS ====================

    async def publish_logistics_task(self, task: dict, status: str) -> Optional[int]:
        """
        Publish or update a logistics task embed in Discord status channel.

        Posts a new message when task is created/approved, then edits the same message
        for subsequent status changes (assigned, completed, cancelled, etc.).

        Returns the message ID if successful.
        """
        config = self.plugin.get_config()
        # Support both 'status_channel' and legacy 'publish_channel'
        channel_id = config.get('status_channel') or config.get('publish_channel')

        if not channel_id:
            return None

        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                log.warning(f"Logistics status channel {channel_id} not found")
                return None

            # Get assigned pilot name if applicable
            assigned_name = task.get('assigned_name')
            if not assigned_name and task.get('assigned_ucid'):
                async with self.apool.connection() as conn:
                    cursor = await conn.execute(
                        "SELECT name FROM players WHERE ucid = %s",
                        (task['assigned_ucid'],)
                    )
                    row = await cursor.fetchone()
                    assigned_name = row[0] if row else None

            # Status colors and emoji
            status_config = {
                'pending': {'color': discord.Color.yellow(), 'emoji': '⏳'},
                'approved': {'color': discord.Color.blue(), 'emoji': '📦'},
                'assigned': {'color': discord.Color.purple(), 'emoji': '🚁'},
                'in_progress': {'color': discord.Color.orange(), 'emoji': '🚁'},
                'completed': {'color': discord.Color.green(), 'emoji': '✅'},
                'cancelled': {'color': discord.Color.red(), 'emoji': '❌'},
                'failed': {'color': discord.Color.dark_red(), 'emoji': '💥'},
            }
            status_info = status_config.get(status, {'color': discord.Color.blue(), 'emoji': '📋'})

            # Priority emoji
            priority_emoji = {
                'urgent': '🔴',
                'high': '🟠',
                'normal': '🟢',
                'low': '⚪',
            }.get(task.get('priority', 'normal'), '🟢')

            embed = discord.Embed(
                title=f"📦 Logistics Task #{task['id']} - {task.get('cargo_type', 'N/A')[:50]}",
                color=status_info['color']
            )

            # Status with emoji
            status_display = f"{status_info['emoji']} {status.upper()}"
            embed.add_field(name='Status', value=status_display, inline=True)
            embed.add_field(name='Priority', value=f"{priority_emoji} {task.get('priority', 'normal').upper()}", inline=True)

            # Coalition
            coalition = task.get('coalition', 2)
            coalition_str = '🔴 RED' if coalition == 1 else '🔵 BLUE'
            embed.add_field(name='Coalition', value=coalition_str, inline=True)

            route_str = f"{task.get('source_name', '?')} → {task.get('destination_name', '?')}"
            embed.add_field(name='Route', value=route_str, inline=False)

            if assigned_name:
                embed.add_field(name='Assigned To', value=assigned_name, inline=True)
            else:
                embed.add_field(name='Assigned To', value='Unassigned', inline=True)

            if task.get('deadline'):
                deadline = task['deadline']
                if isinstance(deadline, datetime):
                    deadline_str = deadline.strftime('%H:%M UTC')
                else:
                    deadline_str = str(deadline)
                embed.add_field(name='Deadline', value=deadline_str, inline=True)

            if task.get('notes'):
                embed.add_field(name='Notes', value=task['notes'][:200], inline=False)

            # Timestamps section
            timestamps = []
            if task.get('created_at'):
                created = task['created_at']
                if isinstance(created, datetime):
                    timestamps.append(f"Created: {created.strftime('%H:%M UTC')}")
            if task.get('assigned_at'):
                assigned = task['assigned_at']
                if isinstance(assigned, datetime):
                    timestamps.append(f"Assigned: {assigned.strftime('%H:%M UTC')}")
            if task.get('completed_at'):
                completed = task['completed_at']
                if isinstance(completed, datetime):
                    timestamps.append(f"Completed: {completed.strftime('%H:%M UTC')}")
            if timestamps:
                embed.add_field(name='Timeline', value=' | '.join(timestamps), inline=False)

            # Footer with server and update time
            footer_parts = []
            if task.get('server_name'):
                footer_parts.append(f"Server: {task['server_name']}")
            footer_parts.append(f"Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
            embed.set_footer(text=' | '.join(footer_parts))

            # Check if we should update an existing message
            existing_msg_id = task.get('discord_message_id')
            if existing_msg_id:
                try:
                    msg = await channel.fetch_message(int(existing_msg_id))
                    await msg.edit(embed=embed)
                    return existing_msg_id
                except discord.NotFound:
                    log.debug(f"Original message {existing_msg_id} not found, posting new message")
                except discord.Forbidden:
                    log.warning(f"Cannot edit message {existing_msg_id} - permission denied")

            # Send new message
            msg = await channel.send(embed=embed)

            # Store message ID in database
            async with self.apool.connection() as conn:
                await conn.execute(
                    "UPDATE logistics_tasks SET discord_message_id = %s WHERE id = %s",
                    (msg.id, task['id'])
                )

            return msg.id

        except Exception as e:
            log.error(f"Error publishing logistics task: {e}")
            return None

    async def _get_available_tasks(self, server_name: str, coalition: int) -> list[dict]:
        """Get tasks available for a coalition."""
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT id, cargo_type, source_name, destination_name, priority, deadline
                FROM logistics_tasks
                WHERE server_name = %s AND coalition = %s
                AND status = 'approved'
                ORDER BY
                    CASE priority
                        WHEN 'urgent' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'normal' THEN 3
                        WHEN 'low' THEN 4
                    END,
                    created_at ASC
            """, (server_name, coalition))
            rows = await cursor.fetchall()
            return [
                {
                    'id': row[0],
                    'cargo_type': row[1],
                    'source_name': row[2],
                    'destination_name': row[3],
                    'priority': row[4],
                    'deadline': row[5]
                }
                for row in rows
            ]

    async def _get_available_tasks_with_positions(self, server_name: str, coalition: int) -> list[dict]:
        """Get tasks available for a coalition with position data for plotting."""
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT t.id, t.cargo_type, t.source_name, t.source_position,
                       t.destination_name, t.destination_position, t.coalition,
                       t.deadline, p.name as assigned_name
                FROM logistics_tasks t
                LEFT JOIN players p ON t.assigned_ucid = p.ucid
                WHERE t.server_name = %s AND t.coalition = %s
                AND t.status IN ('approved', 'assigned', 'in_progress')
            """, (server_name, coalition))
            rows = await cursor.fetchall()
            return [
                {
                    'id': row[0],
                    'cargo_type': row[1],
                    'source_name': row[2],
                    'source_position': row[3],
                    'destination_name': row[4],
                    'destination_position': row[5],
                    'coalition': row[6],
                    'deadline': row[7],
                    'assigned_name': row[8]
                }
                for row in rows
            ]

    async def _get_task_with_position(self, task_id: int, server_name: str, coalition: int) -> dict | None:
        """Get a specific task with position data for plotting."""
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT t.id, t.cargo_type, t.source_name, t.source_position,
                       t.destination_name, t.destination_position, t.coalition,
                       t.deadline, p.name as assigned_name
                FROM logistics_tasks t
                LEFT JOIN players p ON t.assigned_ucid = p.ucid
                WHERE t.id = %s AND t.server_name = %s AND t.coalition = %s
                AND t.status IN ('approved', 'assigned', 'in_progress')
            """, (task_id, server_name, coalition))
            row = await cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'cargo_type': row[1],
                    'source_name': row[2],
                    'source_position': row[3],
                    'destination_name': row[4],
                    'destination_position': row[5],
                    'coalition': row[6],
                    'deadline': row[7],
                    'assigned_name': row[8]
                }
            return None

    async def _get_assigned_task(self, ucid: str, server_name: str) -> dict | None:
        """Get task assigned to a player."""
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT id, cargo_type, source_name, destination_name, deadline, status, priority
                FROM logistics_tasks
                WHERE assigned_ucid = %s AND server_name = %s
                AND status IN ('assigned', 'in_progress')
            """, (ucid, server_name))
            row = await cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'cargo_type': row[1],
                    'source_name': row[2],
                    'destination_name': row[3],
                    'deadline': row[4],
                    'status': row[5],
                    'priority': row[6]
                }
            return None

    async def _get_task_by_id(self, task_id: int, server_name: str, coalition: int) -> dict | None:
        """Get task by ID if visible to coalition."""
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT t.id, t.cargo_type, t.source_name, t.destination_name,
                       t.deadline, t.status, t.priority, p.name as assigned_name
                FROM logistics_tasks t
                LEFT JOIN players p ON t.assigned_ucid = p.ucid
                WHERE t.id = %s AND t.server_name = %s AND t.coalition = %s
            """, (task_id, server_name, coalition))
            row = await cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'cargo_type': row[1],
                    'source_name': row[2],
                    'destination_name': row[3],
                    'deadline': row[4],
                    'status': row[5],
                    'priority': row[6],
                    'assigned_name': row[7]
                }
            return None

    async def _assign_task(self, server: Server, player: Player, task_id: int) -> dict:
        """Assign a task to a player."""
        async with self.apool.connection() as conn:
            async with conn.transaction():
                # Check if task is available
                cursor = await conn.execute("""
                SELECT id, cargo_type, source_name, destination_name, deadline, coalition
                FROM logistics_tasks
                WHERE id = %s AND server_name = %s AND status = 'approved'
            """, (task_id, server.name))
            task = await cursor.fetchone()

            if not task:
                return {'success': False, 'error': 'Task not found or not available'}

            if task[5] != player.side.value:
                return {'success': False, 'error': 'Task is for a different coalition'}

            # Check if player already has a task
            existing = await self._get_assigned_task(player.ucid, server.name)
            if existing:
                return {'success': False, 'error': f'You already have task #{existing["id"]} assigned'}

            # Assign the task
            now = datetime.now(timezone.utc)
            await conn.execute("""
                UPDATE logistics_tasks
                SET assigned_ucid = %s, assigned_at = %s, status = 'assigned', updated_at = %s
                WHERE id = %s
            """, (player.ucid, now, now, task_id))

            # Record history
            await conn.execute("""
                INSERT INTO logistics_tasks_history (task_id, event, actor_ucid, details)
                VALUES (%s, 'assigned', %s, %s)
            """, (task_id, player.ucid, '{"action": "player_accepted"}'))

            # Get full task data for marker creation
            cursor = await conn.execute("""
                SELECT id, cargo_type, source_name, source_position,
                       destination_name, destination_position, coalition, deadline
                FROM logistics_tasks WHERE id = %s
            """, (task_id,))
            full_task = await cursor.fetchone()

            # Create markers for the accepted task
            if full_task:
                task_data = {
                    'id': full_task[0],
                    'cargo_type': full_task[1],
                    'source_name': full_task[2],
                    'source_position': full_task[3],
                    'destination_name': full_task[4],
                    'destination_position': full_task[5],
                    'coalition': full_task[6],
                    'deadline': full_task[7],
                    'assigned_name': player.name
                }
                await self._create_markers_for_task(server, task_data)

            # Publish to status channel
            config = self.get_config(server) or {}
            if config.get('publish_on_assign', True):
                # Fetch full task with discord_message_id
                cursor = await conn.execute("""
                    SELECT id, cargo_type, source_name, destination_name, priority,
                           coalition, deadline, assigned_at, server_name, discord_message_id
                    FROM logistics_tasks WHERE id = %s
                """, (task_id,))
                pub_task = await cursor.fetchone()
                if pub_task:
                    await self.publish_logistics_task({
                        'id': pub_task[0],
                        'cargo_type': pub_task[1],
                        'source_name': pub_task[2],
                        'destination_name': pub_task[3],
                        'priority': pub_task[4],
                        'coalition': pub_task[5],
                        'deadline': pub_task[6],
                        'assigned_at': pub_task[7],
                        'assigned_name': player.name,
                        'server_name': pub_task[8],
                        'discord_message_id': pub_task[9]
                    }, 'assigned')

            return {
                'success': True,
                'cargo': task[1],
                'source': task[2],
                'destination': task[3],
                'deadline': task[4].strftime('%H:%MZ') if task[4] else None
            }

    async def _complete_task(self, server: Server, player: Player, task_id: int) -> dict:
        """Complete a logistics task."""
        async with self.apool.connection() as conn:
            async with conn.transaction():
                # Get task details
                cursor = await conn.execute("""
                SELECT cargo_type, source_name, destination_name
                FROM logistics_tasks
                WHERE id = %s AND assigned_ucid = %s AND status IN ('assigned', 'in_progress')
            """, (task_id, player.ucid))
            task = await cursor.fetchone()

            if not task:
                return {'success': False, 'error': 'Task not found or not assigned to you'}

            now = datetime.now(timezone.utc)

            # Update task status
            await conn.execute("""
                UPDATE logistics_tasks
                SET status = 'completed', completed_at = %s, updated_at = %s
                WHERE id = %s
            """, (now, now, task_id))

            # Record history
            await conn.execute("""
                INSERT INTO logistics_tasks_history (task_id, event, actor_ucid, details)
                VALUES (%s, 'completed', %s, %s)
            """, (task_id, player.ucid, '{"action": "delivery_confirmed"}'))

            # Credit to logbook (if logbook plugin is loaded)
            if 'logbook' in self.bot.plugins:
                try:
                    await conn.execute("""
                        INSERT INTO logbook_logistics_completions
                        (player_ucid, task_id, cargo_type, source_name, destination_name, completed_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (player.ucid, task_id, task[0], task[1], task[2], now))
                except Exception as e:
                    log.warning(f"Failed to credit logbook for task {task_id}: {e}")

            # Remove markers
            await self._remove_task_markers(server, task_id)

            # Publish completion to status channel
            config = self.get_config(server) or {}
            if config.get('publish_on_complete', True):
                cursor = await conn.execute("""
                    SELECT id, cargo_type, source_name, destination_name, priority,
                           coalition, deadline, assigned_at, completed_at, server_name, discord_message_id
                    FROM logistics_tasks WHERE id = %s
                """, (task_id,))
                pub_task = await cursor.fetchone()
                if pub_task:
                    await self.publish_logistics_task({
                        'id': pub_task[0],
                        'cargo_type': pub_task[1],
                        'source_name': pub_task[2],
                        'destination_name': pub_task[3],
                        'priority': pub_task[4],
                        'coalition': pub_task[5],
                        'deadline': pub_task[6],
                        'assigned_at': pub_task[7],
                        'completed_at': pub_task[8],
                        'assigned_name': player.name,
                        'server_name': pub_task[9],
                        'discord_message_id': pub_task[10]
                    }, 'completed')

            return {'success': True}

    async def _abandon_task(self, server: Server, player: Player, task_id: int) -> dict:
        """Abandon a logistics task."""
        async with self.apool.connection() as conn:
            async with conn.transaction():
                now = datetime.now(timezone.utc)

            # Update task - return to approved status
            result = await conn.execute("""
                UPDATE logistics_tasks
                SET assigned_ucid = NULL, assigned_at = NULL, status = 'approved', updated_at = %s
                WHERE id = %s AND assigned_ucid = %s AND status IN ('assigned', 'in_progress')
            """, (now, task_id, player.ucid))

            if result.rowcount == 0:
                return {'success': False, 'error': 'Task not found or not assigned to you'}

            # Record history
            await conn.execute("""
                INSERT INTO logistics_tasks_history (task_id, event, actor_ucid, details)
                VALUES (%s, 'abandoned', %s, %s)
            """, (task_id, player.ucid, '{"action": "player_abandoned"}'))

            # Update markers to remove pilot name
            await self._update_markers_with_pilot(server, task_id, None)

            # Publish abandonment to status channel (back to approved status)
            config = self.get_config(server) or {}
            if config.get('publish_on_abandon', True):
                cursor = await conn.execute("""
                    SELECT id, cargo_type, source_name, destination_name, priority,
                           coalition, deadline, created_at, server_name, discord_message_id
                    FROM logistics_tasks WHERE id = %s
                """, (task_id,))
                pub_task = await cursor.fetchone()
                if pub_task:
                    await self.publish_logistics_task({
                        'id': pub_task[0],
                        'cargo_type': pub_task[1],
                        'source_name': pub_task[2],
                        'destination_name': pub_task[3],
                        'priority': pub_task[4],
                        'coalition': pub_task[5],
                        'deadline': pub_task[6],
                        'created_at': pub_task[7],
                        'assigned_name': None,
                        'server_name': pub_task[8],
                        'discord_message_id': pub_task[9]
                    }, 'approved')  # Back to approved status

            return {'success': True}

    async def _recreate_assigned_task_markers(self, server: Server):
        """Recreate markers only for assigned/in-progress tasks on mission start."""
        async with self.apool.connection() as conn:
            async with conn.transaction():
                # Clear old marker records
                await conn.execute("""
                    DELETE FROM logistics_markers WHERE server_name = %s
                """, (server.name,))

                # Only get tasks that are assigned or in_progress (not approved/pending)
                cursor = await conn.execute("""
                    SELECT t.id, t.cargo_type, t.source_name, t.source_position,
                           t.destination_name, t.destination_position, t.coalition,
                           t.deadline, p.name as assigned_name
                    FROM logistics_tasks t
                    LEFT JOIN players p ON t.assigned_ucid = p.ucid
                    WHERE t.server_name = %s AND t.status IN ('assigned', 'in_progress')
                """, (server.name,))
                tasks = await cursor.fetchall()

        # Create markers outside the transaction (this sends to DCS)
        for task in tasks:
            await self._create_markers_for_task(server, {
                'id': task[0],
                'cargo_type': task[1],
                'source_name': task[2],
                'source_position': task[3],
                'destination_name': task[4],
                'destination_position': task[5],
                'coalition': task[6],
                'deadline': task[7],
                'assigned_name': task[8]
            })

    async def _create_markers_for_task(self, server: Server, task: dict):
        """Send command to DCS to create markers for a task."""
        source_pos = task.get('source_position')
        dest_pos = task.get('destination_position')

        if not source_pos or not dest_pos:
            log.debug(f"Skipping markers for task {task['id']} - missing position data")
            return

        # Handle JSONB position data - could be dict or already parsed
        if isinstance(source_pos, str):
            source_pos = json.loads(source_pos)
        if isinstance(dest_pos, str):
            dest_pos = json.loads(dest_pos)

        deadline_str = task['deadline'].strftime('%H:%MZ') if task.get('deadline') else ""

        await server.send_to_dcs({
            "command": "createLogisticsMarkers",
            "task_id": task['id'],
            "coalition": task['coalition'],
            "source_name": task['source_name'],
            "source_x": source_pos.get('x', 0),
            "source_z": source_pos.get('z', 0),
            "dest_name": task['destination_name'],
            "dest_x": dest_pos.get('x', 0),
            "dest_z": dest_pos.get('z', 0),
            "cargo_type": task['cargo_type'],
            "pilot_name": task.get('assigned_name') or "",
            "deadline": deadline_str,
            "waypoints": "[]"  # TODO: Add waypoint support
        })

    async def _remove_task_markers(self, server: Server, task_id: int):
        """Remove markers for a task."""
        await server.send_to_dcs({
            "command": "removeLogisticsMarkers",
            "task_id": task_id
        })

        async with self.apool.connection() as conn:
            await conn.execute("""
                DELETE FROM logistics_markers WHERE task_id = %s AND server_name = %s
            """, (task_id, server.name))

    async def _remove_markers_after_delay(self, server: Server, task_ids: list[int], delay: int):
        """Remove markers after a delay (for temporary plot markers)."""
        await asyncio.sleep(delay)
        for task_id in task_ids:
            try:
                await server.send_to_dcs({
                    "command": "removeLogisticsMarkers",
                    "task_id": task_id
                })
                log.debug(f"Logistics: Auto-removed markers for task {task_id} after {delay}s")
            except Exception as e:
                log.warning(f"Logistics: Failed to remove markers for task {task_id}: {e}")

    async def _update_markers_with_pilot(self, server: Server, task_id: int, pilot_name: str | None):
        """Update markers with pilot assignment."""
        # For now, just recreate the markers
        # A more efficient implementation would send an update command to DCS
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT id, cargo_type, source_name, source_position,
                       destination_name, destination_position, coalition, deadline
                FROM logistics_tasks WHERE id = %s
            """, (task_id,))
            row = await cursor.fetchone()
            if row:
                await self._remove_task_markers(server, task_id)
                await self._create_markers_for_task(server, {
                    'id': row[0],
                    'cargo_type': row[1],
                    'source_name': row[2],
                    'source_position': row[3],
                    'destination_name': row[4],
                    'destination_position': row[5],
                    'coalition': row[6],
                    'deadline': row[7],
                    'assigned_name': pilot_name
                })

    async def _store_marker_ids(self, server_name: str, task_id: int, marker_ids: list[dict]):
        """Store marker IDs for cleanup."""
        async with self.apool.connection() as conn:
            async with conn.transaction():
                # Clear old markers first
                await conn.execute("""
                DELETE FROM logistics_markers WHERE server_name = %s AND task_id = %s
            """, (server_name, task_id))

            # Store new markers
            for marker in marker_ids:
                marker_id = marker.get('id') if isinstance(marker, dict) else marker
                marker_type = marker.get('type', 'unknown') if isinstance(marker, dict) else 'unknown'
                await conn.execute("""
                    INSERT INTO logistics_markers (server_name, task_id, marker_id, marker_type)
                    VALUES (%s, %s, %s, %s)
                """, (server_name, task_id, marker_id, marker_type))

    async def _notify_player_of_task(self, player: Player, task: dict):
        """Notify player of their assigned task on spawn."""
        deadline_str = task['deadline'].strftime('%H:%MZ') if task.get('deadline') else "None"
        await player.sendPopupMessage(
            f"ACTIVE LOGISTICS TASK\n\n"
            f"Task #{task['id']}\n"
            f"Cargo: {task['cargo_type']}\n"
            f"From: {task['source_name']}\n"
            f"To: {task['destination_name']}\n"
            f"Deadline: {deadline_str}",
            15
        )

    async def _check_delivery_on_landing(self, server: Server, data: dict):
        """Check if a landing event completes a logistics task."""
        # Get player from initiator
        initiator = data.get('initiator', {})
        player_name = initiator.get('name')
        if not player_name:
            return

        player = server.get_player(name=player_name)
        if not player:
            return

        task = await self._get_assigned_task(player.ucid, server.name)
        if not task:
            return

        # Get destination info
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT destination_name, destination_position FROM logistics_tasks WHERE id = %s
            """, (task['id'],))
            row = await cursor.fetchone()
            if not row:
                return

        dest_name = row[0]
        dest_pos = row[1]

        # Check if landed at named destination (preferred - from event.place)
        place = data.get('place', {})
        place_name = place.get('name', '')

        if place_name and dest_name:
            # Use strict airbase name matching to avoid false positives
            if _airbase_names_match(place_name, dest_name):
                log.info(f"Logistics: Auto-completing task {task['id']} for {player.name} at {place_name} (dest: {dest_name})")
                result = await self._complete_task(server, player, task['id'])
                if result['success']:
                    await player.sendChatMessage(f"Delivery confirmed at {place_name}! Task #{task['id']} completed.")
                    await player.sendPopupMessage("DELIVERY COMPLETE\n\nTask logged to your record.", 10)
                return

        # Fallback: check proximity if we have position data
        if dest_pos:
            if isinstance(dest_pos, str):
                dest_pos = json.loads(dest_pos)

            # Get unit position - use the unit_name to query position
            unit_name = initiator.get('unit_name')
            if unit_name:
                # Request position check from DCS
                await self._request_proximity_check(server, task['id'], unit_name, dest_pos)

    async def _request_proximity_check(self, server: Server, task_id: int, unit_name: str, dest_pos: dict):
        """Request proximity check from DCS."""
        await server.send_to_dcs({
            "command": "checkDeliveryProximity",
            "task_id": task_id,
            "unit_name": unit_name,
            "dest_x": dest_pos.get('x', 0),
            "dest_z": dest_pos.get('z', 0),
            "threshold": 3000  # 3km default
        })

    # ==================== F10 MENU METHODS ====================

    async def _create_logistics_menu(self, server: Server, player: Player, group_id: int = None):
        """Create F10 menu for logistics operations."""
        # Use provided group_id or fall back to player object
        if group_id is None:
            group_id = player.group_id
        if not group_id:
            return

        # Build dynamic menu based on available tasks
        tasks = await self._get_available_tasks(server.name, player.side.value)
        tasks_with_pos = await self._get_available_tasks_with_positions(server.name, player.side.value)
        assigned_task = await self._get_assigned_task(player.ucid, server.name)

        # Build menu structure
        menu = [{
            "Logistics": [
                {
                    "View Available Tasks": {
                        "command": "logistics",
                        "params": {"action": "view_tasks"}
                    }
                },
                {
                    "My Current Task": {
                        "command": "logistics",
                        "params": {"action": "my_task"}
                    }
                }
            ]
        }]

        # Add accept task submenu if tasks available and player has no task
        if tasks and not assigned_task:
            accept_menu = []
            for task in tasks[:5]:  # Limit to 5 tasks in menu
                priority_marker = "!" if task['priority'] == 'urgent' else ""
                label = f"#{task['id']}{priority_marker}: {task['cargo_type'][:20]}"
                accept_menu.append({
                    label: {
                        "command": "logistics",
                        "params": {"action": "accept", "task_id": task['id']}
                    }
                })
            menu[0]["Logistics"].append({"Accept Task": accept_menu})

        # Add plot options if there are tasks with positions
        if tasks_with_pos:
            # Plot All option
            menu[0]["Logistics"].append({
                "Plot All Tasks (30s)": {
                    "command": "logistics",
                    "params": {"action": "plot_all"}
                }
            })

            # Plot by ID submenu
            plot_menu = []
            for task in tasks_with_pos[:5]:  # Limit to 5 tasks in menu
                priority_marker = "!" if task.get('priority') == 'urgent' else ""
                label = f"#{task['id']}{priority_marker}: {task['cargo_type'][:20]}"
                plot_menu.append({
                    label: {
                        "command": "logistics",
                        "params": {"action": "plot_task", "task_id": task['id']}
                    }
                })
            menu[0]["Logistics"].append({"Plot Task": plot_menu})

        # Add task actions if player has an assigned task
        if assigned_task:
            menu[0]["Logistics"].append({
                "Mark Delivered": {
                    "command": "logistics",
                    "params": {"action": "deliver"}
                }
            })
            menu[0]["Logistics"].append({
                "Abandon Task": {
                    "command": "logistics",
                    "params": {"action": "abandon"}
                }
            })

        # Send menu to DCS (group_id already validated at start of function)
        await server.send_to_dcs({
            "command": "createMenu",
            "playerID": player.id,
            "groupID": group_id,
            "menu": menu
        })

    async def _menu_view_tasks(self, server: Server, player: Player):
        """Handle 'View Available Tasks' menu option."""
        tasks = await self._get_available_tasks(server.name, player.side.value)
        if not tasks:
            await player.sendPopupMessage("No logistics tasks available.", 10)
            return

        msg = "AVAILABLE LOGISTICS TASKS\n\n"
        for task in tasks[:5]:
            priority_marker = "[!] " if task['priority'] == 'urgent' else ""
            deadline_str = task['deadline'].strftime('%H:%MZ') if task['deadline'] else "None"
            msg += f"{priority_marker}#{task['id']}: {task['cargo_type']}\n"
            msg += f"    {task['source_name']} -> {task['destination_name']}\n"
            msg += f"    Deadline: {deadline_str}\n\n"

        if len(tasks) > 5:
            msg += f"... and {len(tasks) - 5} more tasks"

        await player.sendPopupMessage(msg, 20)

    async def _menu_my_task(self, server: Server, player: Player):
        """Handle 'My Current Task' menu option."""
        task = await self._get_assigned_task(player.ucid, server.name)
        if not task:
            await player.sendPopupMessage("You have no active logistics task.", 10)
            return

        deadline_str = task['deadline'].strftime('%H:%MZ') if task['deadline'] else "None"
        msg = (
            f"YOUR CURRENT TASK\n\n"
            f"Task #{task['id']}\n"
            f"Status: {task['status'].upper()}\n"
            f"Cargo: {task['cargo_type']}\n"
            f"From: {task['source_name']}\n"
            f"To: {task['destination_name']}\n"
            f"Deadline: {deadline_str}"
        )
        await player.sendPopupMessage(msg, 15)

    async def _menu_accept_task(self, server: Server, player: Player, task_id: int):
        """Handle 'Accept Task' menu option."""
        result = await self._assign_task(server, player, task_id)
        if result['success']:
            await player.sendPopupMessage(
                f"TASK #{task_id} ACCEPTED!\n\n"
                f"Cargo: {result['cargo']}\n"
                f"Pickup: {result['source']}\n"
                f"Deliver to: {result['destination']}\n"
                f"Deadline: {result['deadline'] or 'None'}\n\n"
                f"Check your F10 map for route markers.",
                20
            )
            # Refresh menu to show task actions
            await self._create_logistics_menu(server, player)
        else:
            await player.sendPopupMessage(f"Cannot accept task:\n{result['error']}", 10)

    async def _menu_plot_all(self, server: Server, player: Player):
        """Handle 'Plot All Tasks' menu option."""
        tasks = await self._get_available_tasks_with_positions(server.name, player.side.value)
        if not tasks:
            await player.sendPopupMessage("No tasks available to plot.", 10)
            return

        task_ids = []
        for task in tasks:
            await self._create_markers_for_task(server, task)
            task_ids.append(task['id'])

        await player.sendPopupMessage(f"Plotted {len(task_ids)} task(s) on F10 map.\nMarkers will disappear in 30 seconds.", 10)

        # Schedule removal after 30 seconds
        asyncio.create_task(self._remove_markers_after_delay(server, task_ids, 30))

    async def _menu_plot_task(self, server: Server, player: Player, task_id: int):
        """Handle 'Plot Task' menu option for a specific task."""
        task = await self._get_task_with_position(task_id, server.name, player.side.value)
        if not task:
            await player.sendPopupMessage(f"Task #{task_id} not found or not visible.", 10)
            return

        await self._create_markers_for_task(server, task)
        await player.sendPopupMessage(f"Plotted task #{task_id} on F10 map.\nMarkers will disappear in 30 seconds.", 10)

        # Schedule removal after 30 seconds
        asyncio.create_task(self._remove_markers_after_delay(server, [task_id], 30))

    async def _menu_deliver(self, server: Server, player: Player):
        """Handle 'Mark Delivered' menu option."""
        task = await self._get_assigned_task(player.ucid, server.name)
        if not task:
            await player.sendPopupMessage("You have no active task.", 10)
            return

        result = await self._complete_task(server, player, task['id'])
        if result['success']:
            await player.sendPopupMessage(
                f"DELIVERY COMPLETE!\n\n"
                f"Task #{task['id']} completed.\n"
                f"Logged to your pilot record.",
                15
            )
            # Refresh menu
            await self._create_logistics_menu(server, player)
        else:
            await player.sendPopupMessage(f"Cannot complete task:\n{result['error']}", 10)

    async def _menu_abandon(self, server: Server, player: Player):
        """Handle 'Abandon Task' menu option."""
        task = await self._get_assigned_task(player.ucid, server.name)
        if not task:
            await player.sendPopupMessage("You have no active task.", 10)
            return

        result = await self._abandon_task(server, player, task['id'])
        if result['success']:
            await player.sendPopupMessage(
                f"Task #{task['id']} abandoned.\n\n"
                f"Task is now available for others.",
                10
            )
            # Refresh menu
            await self._create_logistics_menu(server, player)
        else:
            await player.sendPopupMessage(f"Cannot abandon task:\n{result['error']}", 10)

    async def _menu_task_details(self, server: Server, player: Player, task_id: int):
        """Handle 'Task Details' menu option."""
        task = await self._get_task_by_id(task_id, server.name, player.side.value)
        if not task:
            await player.sendPopupMessage("Task not found.", 10)
            return

        deadline_str = task['deadline'].strftime('%H:%MZ') if task['deadline'] else "None"
        assigned = task.get('assigned_name') or "Unassigned"
        msg = (
            f"TASK DETAILS\n\n"
            f"Task #{task['id']} ({task['status'].upper()})\n"
            f"Priority: {task['priority'].upper()}\n"
            f"Cargo: {task['cargo_type']}\n"
            f"From: {task['source_name']}\n"
            f"To: {task['destination_name']}\n"
            f"Deadline: {deadline_str}\n"
            f"Assigned: {assigned}"
        )
        await player.sendPopupMessage(msg, 15)

    async def _cancel_stale_tasks(self, server: Server):
        """Cancel tasks that have been pending/approved for too long."""
        config = self.get_config(server) or {}
        tasks_config = config.get('tasks', {})
        stale_days = tasks_config.get('stale_days', 7)  # Default 7 days

        if stale_days <= 0:
            return  # Disabled

        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

        async with self.apool.connection() as conn:
            async with conn.transaction():
                # Find stale tasks
                cursor = await conn.execute("""
                    SELECT id FROM logistics_tasks
                    WHERE server_name = %s
                    AND status IN ('pending', 'approved')
                    AND created_at < %s
                """, (server.name, cutoff))
                stale_tasks = await cursor.fetchall()

                if not stale_tasks:
                    return

                task_ids = [row[0] for row in stale_tasks]
                log.info(f"Logistics: Cancelling {len(task_ids)} stale tasks on {server.name}: {task_ids}")

                # Cancel the tasks
                await conn.execute("""
                    UPDATE logistics_tasks
                    SET status = 'cancelled', updated_at = %s
                    WHERE id = ANY(%s)
                """, (datetime.now(timezone.utc), task_ids))

                # Record history
                for task_id in task_ids:
                    await conn.execute("""
                        INSERT INTO logistics_tasks_history (task_id, event, details)
                        VALUES (%s, 'cancelled', %s)
                    """, (task_id, json.dumps({'reason': f'stale_after_{stale_days}_days', 'auto': True})))
