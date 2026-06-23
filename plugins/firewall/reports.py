import pandas as pd
import seaborn as sns

from psycopg.rows import dict_row

from core import EmbedElement, report
from plugins.userstats.filter import StatisticsFilter


class DDoSStats(EmbedElement):

    async def render(self, server_name: str | None, period: StatisticsFilter):
        where_clause = "AND server_name = %(server_name)s" if server_name else ""
        sql = f"""
            SELECT time, port, protocol, connections, unique_ips, players,
                   non_player_udp_ips, under_attack
            FROM port_traffic
            WHERE {period.filter(self.env.bot)}
            {where_clause}
            ORDER BY time DESC
            LIMIT 500
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                rows = await cursor.fetchall()

        if not rows:
            self.add_field(name='No port traffic data available.',
                           value='Enable ddos_detect in config/services/monitoring.yaml to start collecting.')
            return

        # Current state: latest row per (port, protocol)
        latest: dict[tuple[int, str], dict] = {}
        for row in rows:
            key = (row['port'], row['protocol'])
            if key not in latest:
                latest[key] = row

        # Build status lines
        status_lines = []
        for (port, proto), row in sorted(latest.items()):
            state = '🔴 UNDER ATTACK' if row['under_attack'] else '🟢 Normal'
            if proto == 'udp':
                # UDP: show non-player source IPs (from scapy sniff), unique_ips is always 0
                line = (
                    f"**{proto.upper()}/{port}** — {state}\n"
                    f"├ Connections: {row['connections']} │ Players: {row['players']} │ "
                    f"Non-player UDP: {row['non_player_udp_ips']}"
                )
            else:
                # TCP: show unique remote IPs
                line = (
                    f"**{proto.upper()}/{port}** — {state}\n"
                    f"├ Connections: {row['connections']} │ Players: {row['players']} │ "
                    f"Unique IPs: {row['unique_ips']}"
                )
            status_lines.append(line)

        self.add_field(name='Current DDoS State', value='\n'.join(status_lines) or 'No data', inline=False)

        # Attack summary: count attack rows vs. normal rows in a period
        attack_rows = [r for r in rows if r['under_attack']]
        normal_rows = [r for r in rows if not r['under_attack']]

        if attack_rows:
            attack_times = sorted(set(r['time'] for r in attack_rows))
            first_attack = min(attack_times)
            last_attack = max(attack_times)
            peak = max(attack_rows, key=lambda r: r['connections'])

            summary = (
                f"**Attack Periods:** {len(attack_times)} tick(s) with attack flag\n"
                f"├ First: {first_attack.strftime('%Y-%m-%d %H:%M')}\n"
                f"├ Last: {last_attack.strftime('%Y-%m-%d %H:%M')}\n"
                f"├ Peak connections: {peak['connections']} "
                f"({peak['protocol'].upper()}/{peak['port']})\n"
                f"└ Total rows: {len(normal_rows)} normal, {len(attack_rows)} attack"
            )
            self.add_field(name='Attack Summary', value=summary, inline=False)
        else:
            self.add_field(name='Attack Summary',
                           value=f'✅ No attacks detected in the selected period.\n'
                                 f'({len(normal_rows)} data points collected)',
                           inline=False)

        # Connection stats per port/protocol (avg over non-attack rows)
        non_attack = [r for r in rows if not r['under_attack']]
        if non_attack:
            port_agg: dict[tuple[int, str], dict] = {}
            for r in non_attack:
                key = (r['port'], r['protocol'])
                if key not in port_agg:
                    port_agg[key] = {'connections': [], 'unique_ips': [], 'non_player_udp_ips': []}
                port_agg[key]['connections'].append(r['connections'])
                port_agg[key]['unique_ips'].append(r['unique_ips'])
                port_agg[key]['non_player_udp_ips'].append(r['non_player_udp_ips'])

            baseline_lines = []
            for (port, proto), data in sorted(port_agg.items()):
                avg_conn = sum(data['connections']) / len(data['connections'])
                if proto == 'udp':
                    avg_udp = sum(data['non_player_udp_ips']) / len(data['non_player_udp_ips'])
                    baseline_lines.append(
                        f"**{proto.upper()}/{port}** — avg {avg_conn:.1f} conns, {avg_udp:.1f} non-player UDP"
                    )
                else:
                    avg_ips = sum(data['unique_ips']) / len(data['unique_ips'])
                    baseline_lines.append(
                        f"**{proto.upper()}/{port}** — avg {avg_conn:.1f} conns, {avg_ips:.1f} unique IPs"
                    )
            self.add_field(name='Baseline (non-attack)',
                           value='\n'.join(baseline_lines) or 'No baseline data',
                           inline=False)
            await report.Ruler(self.env).render(ruler_length=25)


class DDoSGraph(report.MultiGraphElement):

    async def render(self, server_name: str | None, period: StatisticsFilter):
        where_clause = "AND server_name = %(server_name)s" if server_name else ""
        sql = f"""
            SELECT time, port, protocol, connections, unique_ips, players,
                   non_player_udp_ips, under_attack
            FROM port_traffic
            WHERE {period.filter(self.env.bot)}
            {where_clause}
            ORDER BY time ASC
            LIMIT 500
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                rows = await cursor.fetchall()

        if not rows:
            self.env.figure = None
            return

        # --- Axis 0: TCP connections ---
        tcp_rows = [r for r in rows if r['protocol'] == 'tcp']
        if tcp_rows:
            df_tcp = pd.DataFrame(tcp_rows)
            df_tcp['time'] = pd.to_datetime(df_tcp['time'])

            self.axes[0].set_title('TCP Connections', color='white', fontsize=14)
            self.axes[0].set_ylabel('Connections', color='white', fontsize=10)
            self.axes[0].tick_params(axis='x', colors='white', rotation=30)
            self.axes[0].tick_params(axis='y', colors='white')
            self.axes[0].set_facecolor('#303030')
            for spine in self.axes[0].spines.values():
                spine.set_color('white')
            self.axes[0].spines['top'].set_visible(False)
            self.axes[0].spines['right'].set_visible(False)

            # Plot normal points
            normal_tcp = df_tcp[~df_tcp['under_attack']]
            attack_tcp = df_tcp[df_tcp['under_attack']]

            if not normal_tcp.empty:
                sns.lineplot(data=normal_tcp, x='time', y='connections',
                             ax=self.axes[0], color='dodgerblue', label='Normal')
            if not attack_tcp.empty:
                sns.scatterplot(data=attack_tcp, x='time', y='connections',
                                ax=self.axes[0], color='red', s=60, zorder=5,
                                label='Under Attack')

            # Highlight attack background regions
            attack_times = sorted(attack_tcp['time'].unique()) if not attack_tcp.empty else []
            if attack_times:
                # Group consecutive attack times into regions
                regions = []
                region_start = attack_times[0]
                prev = attack_times[0]
                for t in attack_times[1:]:
                    if (t - prev).total_seconds() > 120:  # gap > 2 min = new region
                        regions.append((region_start, prev))
                        region_start = t
                    prev = t
                regions.append((region_start, prev))
                for start, end in regions:
                    self.axes[0].axvspan(start, end, alpha=0.15, color='red', zorder=0)

            self.axes[0].legend(loc='upper left', fontsize=9)
        else:
            self.axes[0].text(0.5, 0.5, 'No TCP data.', ha='center', va='center',
                              fontsize=15, color='white')
            self.axes[0].set_xticks([])
            self.axes[0].set_yticks([])

        # --- Axis 1: UDP non-player source IPs ---
        udp_rows = [r for r in rows if r['protocol'] == 'udp']
        if udp_rows:
            df_udp = pd.DataFrame(udp_rows)
            df_udp['time'] = pd.to_datetime(df_udp['time'])

            self.axes[1].set_title('UDP Non-Player Sources', color='white', fontsize=14)
            self.axes[1].set_ylabel('Unique IPs', color='white', fontsize=10)
            self.axes[1].tick_params(axis='x', colors='white', rotation=30)
            self.axes[1].tick_params(axis='y', colors='white')
            self.axes[1].set_facecolor('#303030')
            for spine in self.axes[1].spines.values():
                spine.set_color('white')
            self.axes[1].spines['top'].set_visible(False)
            self.axes[1].spines['right'].set_visible(False)

            normal_udp = df_udp[~df_udp['under_attack']]
            attack_udp = df_udp[df_udp['under_attack']]

            if not normal_udp.empty:
                sns.lineplot(data=normal_udp, x='time', y='non_player_udp_ips',
                             ax=self.axes[1], color='orange', label='Normal')
            if not attack_udp.empty:
                sns.scatterplot(data=attack_udp, x='time', y='non_player_udp_ips',
                                ax=self.axes[1], color='red', s=60, zorder=5,
                                label='Under Attack')

            # Highlight attack background regions
            attack_times_udp = sorted(attack_udp['time'].unique()) if not attack_udp.empty else []
            if attack_times_udp:
                regions = []
                region_start = attack_times_udp[0]
                prev = attack_times_udp[0]
                for t in attack_times_udp[1:]:
                    if (t - prev).total_seconds() > 120:
                        regions.append((region_start, prev))
                        region_start = t
                    prev = t
                regions.append((region_start, prev))
                for start, end in regions:
                    self.axes[1].axvspan(start, end, alpha=0.15, color='red', zorder=0)

            self.axes[1].legend(loc='upper left', fontsize=9)
        else:
            self.axes[1].text(0.5, 0.5, 'No UDP data.', ha='center', va='center',
                              fontsize=15, color='white')
            self.axes[1].set_xticks([])
            self.axes[1].set_yticks([])
