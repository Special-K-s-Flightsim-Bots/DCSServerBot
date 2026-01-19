import discord
import json
import logging

from core import Plugin, utils, Server, Status, Group
from datetime import datetime, timezone
from discord import app_commands
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from typing import Literal, Optional

from .listener import LogisticsEventListener

log = logging.getLogger(__name__)


# ==================== AUTOCOMPLETE FUNCTIONS ====================

async def logistics_task_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for logistics tasks."""
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT t.id, t.cargo_type, t.destination_name, t.status
                FROM logistics_tasks t
                WHERE (CAST(t.id AS TEXT) LIKE %s OR t.cargo_type ILIKE %s OR t.destination_name ILIKE %s)
                ORDER BY t.created_at DESC LIMIT 25
            """, ('%' + current + '%', '%' + current + '%', '%' + current + '%'))
            return [
                app_commands.Choice(
                    name=f"#{row[0]} - {row[1][:30]} -> {row[2]} ({row[3]})",
                    value=row[0]
                )
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def pending_task_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for pending logistics tasks."""
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT t.id, t.cargo_type, t.destination_name, p.name
                FROM logistics_tasks t
                JOIN players p ON t.created_by_ucid = p.ucid
                WHERE t.status = 'pending'
                AND (CAST(t.id AS TEXT) LIKE %s OR t.cargo_type ILIKE %s OR t.destination_name ILIKE %s)
                ORDER BY t.created_at ASC LIMIT 25
            """, ('%' + current + '%', '%' + current + '%', '%' + current + '%'))
            return [
                app_commands.Choice(
                    name=f"#{row[0]} - {row[1][:25]} (by {row[3]})",
                    value=row[0]
                )
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []




class Logistics(Plugin[LogisticsEventListener]):
    """
    Logistics mission system plugin.

    Provides comprehensive logistics task management with in-game integration:
    - Create and manage logistics delivery tasks
    - In-game chat commands for pilots to accept/complete tasks
    - F10 map markers showing routes
    - Warehouse inventory queries
    - Integration with pilot logbook
    """

    # Command group "/logistics"
    logistics = Group(name="logistics", description="Logistics mission management")

    # Command group "/warehouse"
    warehouse = Group(name="warehouse", description="Warehouse inventory commands")

    # ==================== LOGISTICS COMMANDS ====================

    @logistics.command(description='Create a new logistics task')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.rename(source_idx='source', dest_idx='destination')
    @app_commands.describe(source_idx='Pickup location (airbase/FARP/carrier)')
    @app_commands.describe(dest_idx='Delivery location')
    @app_commands.autocomplete(source_idx=utils.airbase_autocomplete, dest_idx=utils.airbase_autocomplete)
    async def create(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer],
                     source_idx: int,
                     dest_idx: int,
                     cargo: str,
                     priority: Literal['low', 'normal', 'high', 'urgent'] = 'normal',
                     coalition: Literal['red', 'blue'] = 'blue',
                     deadline: Optional[str] = None):
        """
        Create a logistics task directly (pre-approved).

        Parameters
        ----------
        server: The server to create the task on
        source_idx: Pickup location (airbase/FARP/carrier)
        dest_idx: Delivery location
        cargo: Description of cargo to deliver
        priority: Task priority (affects sorting)
        coalition: Which coalition can see/accept the task
        deadline: Optional deadline in HH:MM format (UTC)
        """
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        # Get airbase names from indices
        if not server.current_mission or not server.current_mission.airbases:
            await interaction.followup.send("Server has no mission loaded or no airbases available.", ephemeral=True)
            return

        try:
            source_airbase = server.current_mission.airbases[source_idx]
            dest_airbase = server.current_mission.airbases[dest_idx]
        except IndexError:
            await interaction.followup.send("Invalid airbase selection.", ephemeral=True)
            return

        source = source_airbase['name']
        destination = dest_airbase['name']
        # Serialize positions to JSON for JSONB columns
        source_position = json.dumps(source_airbase.get('position')) if source_airbase.get('position') else None
        dest_position = json.dumps(dest_airbase.get('position')) if dest_airbase.get('position') else None

        coalition_id = 1 if coalition == 'red' else 2

        # Parse deadline if provided
        deadline_dt = None
        if deadline:
            try:
                now = datetime.now(timezone.utc)
                deadline_dt = datetime.strptime(deadline, '%H:%M').replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=timezone.utc
                )
                # If time has passed today, assume tomorrow
                if deadline_dt < now:
                    from datetime import timedelta
                    deadline_dt += timedelta(days=1)
            except ValueError:
                await interaction.followup.send("Invalid deadline format. Use HH:MM (UTC).", ephemeral=True)
                return

        async with self.apool.connection() as conn:
            now = datetime.now(timezone.utc)
            cursor = await conn.execute("""
                INSERT INTO logistics_tasks
                (server_name, created_by_ucid, status, priority, cargo_type,
                 source_name, source_position, destination_name, destination_position,
                 coalition, deadline, approved_by, approved_at, created_at, updated_at)
                VALUES (%s, %s, 'approved', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                server.name,
                None,  # NULL for admin-created tasks (no player UCID)
                priority,
                cargo,
                source,
                source_position,
                destination,
                dest_position,
                coalition_id,
                deadline_dt,
                str(interaction.user.id),
                now,
                now,
                now
            ))
            row = await cursor.fetchone()
            task_id = row[0]

            # Record history
            await conn.execute("""
                INSERT INTO logistics_tasks_history (task_id, event, actor_discord_id, details)
                VALUES (%s, 'created', %s, %s)
            """, (task_id, interaction.user.id, '{"source": "discord_admin", "auto_approved": true}'))

            # Publish to status channel if configured
            config = self.get_config(server)
            if config.get('publish_on_create', True):
                task_data = {
                    'id': task_id,
                    'server_name': server.name,
                    'cargo_type': cargo,
                    'source_name': source,
                    'destination_name': destination,
                    'priority': priority,
                    'coalition': coalition_id,
                    'deadline': deadline_dt,
                    'status': 'approved',
                    'created_at': now,
                    'discord_message_id': None
                }
                await self.listener.publish_logistics_task(task_data, 'approved')

        # Markers are created when a player accepts the task or uses -plot command

        embed = discord.Embed(
            title="Logistics Task Created",
            description=f"Task #{task_id} is now available for pilots.",
            color=discord.Color.green()
        )
        embed.add_field(name="Cargo", value=cargo, inline=True)
        embed.add_field(name="From", value=source, inline=True)
        embed.add_field(name="To", value=destination, inline=True)
        embed.add_field(name="Priority", value=priority.upper(), inline=True)
        embed.add_field(name="Coalition", value=coalition.upper(), inline=True)
        if deadline_dt:
            embed.add_field(name="Deadline", value=deadline_dt.strftime('%H:%M UTC'), inline=True)

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @logistics.command(description='List logistics tasks')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def list(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer] | None = None,
                   status: Literal['all', 'pending', 'approved', 'assigned', 'completed'] = 'approved'):
        """
        List logistics tasks with optional filtering.

        Parameters
        ----------
        server: Filter by server (optional)
        status: Filter by status
        """
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        async with self.apool.connection() as conn:
            if status == 'all':
                status_filter = ""
                params = []
            else:
                status_filter = "WHERE t.status = %s"
                params = [status]

            if server:
                if status_filter:
                    status_filter += " AND t.server_name = %s"
                else:
                    status_filter = "WHERE t.server_name = %s"
                params.append(server.name)

            cursor = await conn.execute(f"""
                SELECT t.id, t.cargo_type, t.source_name, t.destination_name,
                       t.status, t.priority, t.deadline, t.server_name,
                       p.name as assigned_name
                FROM logistics_tasks t
                LEFT JOIN players p ON t.assigned_ucid = p.ucid
                {status_filter}
                ORDER BY t.created_at DESC
                LIMIT 20
            """, params)
            tasks = await cursor.fetchall()

        if not tasks:
            await interaction.followup.send("No logistics tasks found.", ephemeral=ephemeral)
            return

        embed = discord.Embed(
            title="Logistics Tasks",
            color=discord.Color.blue()
        )

        for task in tasks:
            priority_emoji = {
                'urgent': '!',
                'high': '^',
                'normal': '-',
                'low': 'v'
            }.get(task[5], '-')

            deadline_str = task[6].strftime('%H:%MZ') if task[6] else "None"
            assigned = task[8] or "Unassigned"

            embed.add_field(
                name=f"{priority_emoji} #{task[0]} - {task[4].upper()}",
                value=f"**Cargo:** {task[1][:40]}\n"
                      f"**Route:** {task[2]} → {task[3]}\n"
                      f"**Assigned:** {assigned}\n"
                      f"**Deadline:** {deadline_str}",
                inline=True
            )

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @logistics.command(description='View details of a logistics task')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(task_id=logistics_task_autocomplete)
    async def view(self, interaction: discord.Interaction, task_id: int):
        """View detailed task information including history."""
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT t.*, p1.name as created_by_name, p2.name as assigned_name
                    FROM logistics_tasks t
                    LEFT JOIN players p1 ON t.created_by_ucid = p1.ucid
                    LEFT JOIN players p2 ON t.assigned_ucid = p2.ucid
                    WHERE t.id = %s
                """, (task_id,))
                task = await cursor.fetchone()

                if not task:
                    await interaction.followup.send(f"Task #{task_id} not found.", ephemeral=True)
                    return

                # Get history
                await cursor.execute("""
                    SELECT event, created_at, details
                    FROM logistics_tasks_history
                    WHERE task_id = %s
                    ORDER BY created_at DESC
                    LIMIT 10
                """, (task_id,))
                history = await cursor.fetchall()

        status_colors = {
            'pending': discord.Color.yellow(),
            'approved': discord.Color.blue(),
            'assigned': discord.Color.purple(),
            'in_progress': discord.Color.orange(),
            'completed': discord.Color.green(),
            'failed': discord.Color.red(),
            'cancelled': discord.Color.greyple()
        }

        embed = discord.Embed(
            title=f"Logistics Task #{task_id}",
            color=status_colors.get(task['status'], discord.Color.blue())
        )
        embed.add_field(name="Status", value=task['status'].upper(), inline=True)
        embed.add_field(name="Priority", value=task['priority'].upper(), inline=True)
        embed.add_field(name="Coalition", value="RED" if task['coalition'] == 1 else "BLUE", inline=True)
        embed.add_field(name="Cargo", value=task['cargo_type'], inline=False)
        embed.add_field(name="Source", value=task['source_name'], inline=True)
        embed.add_field(name="Destination", value=task['destination_name'], inline=True)

        if task['deadline']:
            embed.add_field(name="Deadline", value=task['deadline'].strftime('%Y-%m-%d %H:%M UTC'), inline=True)

        embed.add_field(name="Created By", value=task['created_by_name'] or 'Admin', inline=True)
        embed.add_field(name="Assigned To", value=task['assigned_name'] or 'Unassigned', inline=True)
        embed.add_field(name="Server", value=task['server_name'], inline=True)

        if task['notes']:
            embed.add_field(name="Notes", value=task['notes'], inline=False)

        # Add history
        if history:
            history_text = "\n".join([
                f"• {h['event']} at {h['created_at'].strftime('%H:%M UTC')}"
                for h in history[:5]
            ])
            embed.add_field(name="Recent History", value=history_text, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @logistics.command(description='Approve a pending logistics request')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(task_id=pending_task_autocomplete)
    async def approve(self, interaction: discord.Interaction,
                      task_id: int,
                      source: Optional[str] = None,
                      notes: Optional[str] = None):
        """
        Approve a pending task - makes it available for in-game acceptance.

        Parameters
        ----------
        task_id: The task to approve
        source: Pickup location (if not already set)
        notes: Optional approval notes
        """
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        async with self.apool.connection() as conn:
            # Get task
            cursor = await conn.execute("""
                SELECT id, status, source_name, server_name, destination_name,
                       cargo_type, coalition, deadline
                FROM logistics_tasks WHERE id = %s
            """, (task_id,))
            task = await cursor.fetchone()

            if not task:
                await interaction.followup.send(f"Task #{task_id} not found.", ephemeral=True)
                return

            if task[1] != 'pending':
                await interaction.followup.send(f"Task #{task_id} is not pending (status: {task[1]}).", ephemeral=True)
                return

            # Update source if provided
            final_source = source if source else task[2]
            if final_source == 'TBD':
                await interaction.followup.send("Please specify a source location for this task.", ephemeral=True)
                return

            now = datetime.now(timezone.utc)

            # Get source position
            server = self.bot.servers.get(task[3])
            source_pos = None
            if server and server.current_mission:
                for ab in server.current_mission.airbases:
                    if ab['name'] == final_source:
                        source_pos = ab.get('position')
                        break

            await conn.execute("""
                UPDATE logistics_tasks
                SET status = 'approved', source_name = %s, source_position = %s,
                    approved_by = %s, approved_at = %s, notes = %s, updated_at = %s
                WHERE id = %s
            """, (final_source, source_pos, str(interaction.user.id), now, notes, now, task_id))

            # Record history
            await conn.execute("""
                INSERT INTO logistics_tasks_history (task_id, event, actor_discord_id, details)
                VALUES (%s, 'approved', %s, %s)
            """, (task_id, interaction.user.id, f'{{"notes": "{notes or ""}"}}'))

        # Create markers if server is running
        if server and server.status == Status.RUNNING and source_pos:
            dest_pos = None
            for ab in server.current_mission.airbases:
                if ab['name'] == task[4]:
                    dest_pos = ab.get('position')
                    break

            if dest_pos:
                await self.listener._create_markers_for_task(server, {
                    'id': task_id,
                    'cargo_type': task[5],
                    'source_name': final_source,
                    'source_position': source_pos,
                    'destination_name': task[4],
                    'destination_position': dest_pos,
                    'coalition': task[6],
                    'deadline': task[7],
                    'assigned_name': None
                })

        embed = discord.Embed(
            title="Task Approved",
            description=f"Task #{task_id} is now available for pilots to accept.",
            color=discord.Color.green()
        )
        embed.add_field(name="Source", value=final_source, inline=True)
        embed.add_field(name="Destination", value=task[4], inline=True)

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @logistics.command(description='Deny a pending logistics request')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(task_id=pending_task_autocomplete)
    async def deny(self, interaction: discord.Interaction,
                   task_id: int,
                   reason: str):
        """
        Deny a pending request.

        Parameters
        ----------
        task_id: The task to deny
        reason: Reason for denial
        """
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        async with self.apool.connection() as conn:
            now = datetime.now(timezone.utc)

            result = await conn.execute("""
                UPDATE logistics_tasks
                SET status = 'cancelled', notes = %s, updated_at = %s
                WHERE id = %s AND status = 'pending'
            """, (f"Denied: {reason}", now, task_id))

            if result.rowcount == 0:
                await interaction.followup.send(f"Task #{task_id} not found or not pending.", ephemeral=True)
                return

            await conn.execute("""
                INSERT INTO logistics_tasks_history (task_id, event, actor_discord_id, details)
                VALUES (%s, 'denied', %s, %s)
            """, (task_id, interaction.user.id, f'{{"reason": "{reason}"}}'))

        await interaction.followup.send(f"Task #{task_id} has been denied.", ephemeral=ephemeral)

    @logistics.command(description='Cancel an active logistics task')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(task_id=logistics_task_autocomplete)
    async def cancel(self, interaction: discord.Interaction,
                     task_id: int,
                     reason: Optional[str] = None):
        """
        Cancel a task (any status except completed).

        Parameters
        ----------
        task_id: The task to cancel
        reason: Optional reason for cancellation
        """
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        async with self.apool.connection() as conn:
            # Get task for server info and publishing
            cursor = await conn.execute("""
                SELECT server_name, cargo_type, source_name, destination_name, priority,
                       coalition, deadline, created_at, discord_message_id
                FROM logistics_tasks WHERE id = %s AND status != 'completed'
            """, (task_id,))
            task = await cursor.fetchone()

            if not task:
                await interaction.followup.send(f"Task #{task_id} not found or already completed.", ephemeral=True)
                return

            now = datetime.now(timezone.utc)

            await conn.execute("""
                UPDATE logistics_tasks
                SET status = 'cancelled', notes = %s, updated_at = %s
                WHERE id = %s
            """, (f"Cancelled: {reason or 'No reason given'}", now, task_id))

            await conn.execute("""
                INSERT INTO logistics_tasks_history (task_id, event, actor_discord_id, details)
                VALUES (%s, 'cancelled', %s, %s)
            """, (task_id, interaction.user.id, f'{{"reason": "{reason or ""}"}}'))

        # Remove markers
        server = self.bot.servers.get(task[0])
        if server:
            await self.listener._remove_task_markers(server, task_id)

            # Publish cancellation to status channel
            config = self.get_config(server)
            if config.get('publish_on_cancel', True):
                await self.listener.publish_logistics_task({
                    'id': task_id,
                    'cargo_type': task[1],
                    'source_name': task[2],
                    'destination_name': task[3],
                    'priority': task[4],
                    'coalition': task[5],
                    'deadline': task[6],
                    'created_at': task[7],
                    'notes': f"Cancelled: {reason or 'No reason given'}",
                    'server_name': task[0],
                    'discord_message_id': task[8]
                }, 'cancelled')

        await interaction.followup.send(f"Task #{task_id} has been cancelled.", ephemeral=ephemeral)

    # ==================== WAREHOUSE COMMANDS ====================

    @warehouse.command(description='Query warehouse inventory at a location')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.rename(airbase_idx='airbase')
    @app_commands.describe(airbase_idx='Airbase or carrier to query')
    @app_commands.autocomplete(airbase_idx=utils.airbase_autocomplete)
    async def status(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                     airbase_idx: int,
                     category: Literal['all', 'aircraft', 'weapon', 'liquids'] = 'all'):
        """
        Query warehouse inventory at an airbase or carrier.

        Parameters
        ----------
        server: The server to query
        airbase_idx: Airbase or carrier to query
        category: Filter by category
        """
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        # Get airbase name from index
        if not server.current_mission or not server.current_mission.airbases:
            await interaction.followup.send("Server has no mission loaded or no airbases available.", ephemeral=True)
            return

        try:
            airbase_data = server.current_mission.airbases[airbase_idx]
            airbase = airbase_data['name']
        except IndexError:
            await interaction.followup.send("Invalid airbase selection.", ephemeral=True)
            return

        try:
            data = await server.send_to_dcs_sync({
                "command": "getAirbase",
                "name": airbase
            }, timeout=60)
        except Exception as e:
            await interaction.followup.send(f"Failed to query warehouse: {e}", ephemeral=True)
            return

        if not data or 'warehouse' not in data:
            await interaction.followup.send(f"No warehouse data for {airbase}.", ephemeral=True)
            return

        warehouse = data['warehouse']
        unlimited = data.get('unlimited', {})

        embed = discord.Embed(
            title=f"Warehouse: {airbase}",
            description=f"Server: {server.name}",
            color=discord.Color.blue()
        )

        # Liquids
        if category in ('all', 'liquids') and 'liquids' in warehouse:
            liquid_names = {0: 'Jet Fuel', 1: 'Avgas', 2: 'MW-50', 3: 'Diesel'}
            liquids_text = ""
            for lid, amount in warehouse['liquids'].items():
                name = liquid_names.get(int(lid), f"Liquid {lid}")
                amount_kg = amount / 1000  # Convert to tons
                liquids_text += f"• {name}: {amount_kg:.1f}t\n"

            if unlimited.get('liquids'):
                liquids_text += "\n*Unlimited*"

            if liquids_text:
                embed.add_field(name="Liquids", value=liquids_text[:1024], inline=False)

        # Weapons
        if category in ('all', 'weapon') and 'weapon' in warehouse:
            weapons_text = ""
            items = list(warehouse['weapon'].items())[:15]  # Limit to 15
            for wtype, qty in items:
                # Simplify weapon name
                short_name = wtype.split('.')[-1].replace('_', ' ')
                weapons_text += f"• {short_name}: {qty}\n"

            if len(warehouse['weapon']) > 15:
                weapons_text += f"\n... and {len(warehouse['weapon']) - 15} more"

            if unlimited.get('weapon'):
                weapons_text += "\n*Unlimited*"

            if weapons_text:
                embed.add_field(name="Weapons", value=weapons_text[:1024], inline=False)

        # Aircraft
        if category in ('all', 'aircraft') and 'aircraft' in warehouse:
            aircraft_text = ""
            for atype, qty in warehouse['aircraft'].items():
                aircraft_text += f"• {atype}: {qty}\n"

            if unlimited.get('aircraft'):
                aircraft_text += "\n*Unlimited*"

            if aircraft_text:
                embed.add_field(name="Aircraft", value=aircraft_text[:1024], inline=False)

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @warehouse.command(description='Compare inventory between two locations')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.rename(source_idx='source', dest_idx='destination')
    @app_commands.describe(source_idx='First location to compare')
    @app_commands.describe(dest_idx='Second location to compare')
    @app_commands.autocomplete(source_idx=utils.airbase_autocomplete, dest_idx=utils.airbase_autocomplete)
    async def compare(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                      source_idx: int,
                      dest_idx: int):
        """
        Compare warehouse inventories between two locations.

        Parameters
        ----------
        server: The server to query
        source_idx: First location to compare
        dest_idx: Second location to compare
        """
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        # Get airbase names from indices
        if not server.current_mission or not server.current_mission.airbases:
            await interaction.followup.send("Server has no mission loaded or no airbases available.", ephemeral=True)
            return

        try:
            source = server.current_mission.airbases[source_idx]['name']
            destination = server.current_mission.airbases[dest_idx]['name']
        except IndexError:
            await interaction.followup.send("Invalid airbase selection.", ephemeral=True)
            return

        try:
            source_data = await server.send_to_dcs_sync({
                "command": "getAirbase",
                "name": source
            }, timeout=60)
            dest_data = await server.send_to_dcs_sync({
                "command": "getAirbase",
                "name": destination
            }, timeout=60)
        except Exception as e:
            await interaction.followup.send(f"Failed to query warehouses: {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title="Warehouse Comparison",
            description=f"{source} vs {destination}",
            color=discord.Color.blue()
        )

        # Compare liquids
        liquid_names = {0: 'Jet Fuel', 1: 'Avgas', 2: 'MW-50', 3: 'Diesel'}
        source_liquids = source_data.get('warehouse', {}).get('liquids', {})
        dest_liquids = dest_data.get('warehouse', {}).get('liquids', {})

        liquids_text = ""
        for lid in set(list(source_liquids.keys()) + list(dest_liquids.keys())):
            name = liquid_names.get(int(lid), f"Liquid {lid}")
            src_amt = source_liquids.get(lid, 0) / 1000
            dst_amt = dest_liquids.get(lid, 0) / 1000
            liquids_text += f"• {name}: {src_amt:.1f}t / {dst_amt:.1f}t\n"

        if liquids_text:
            embed.add_field(name="Liquids (Source / Dest)", value=liquids_text, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Logistics(bot, LogisticsEventListener))
