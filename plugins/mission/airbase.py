import discord

from collections import defaultdict
from core import report, get_translation, utils, Side, const, Server, Coalition
from typing import Counter

from .const import LIQUIDS

_ = get_translation(__name__.split('.')[1])


class Info(report.EmbedElement):

    @staticmethod
    def render_liquids(embed: discord.Embed, data: dict):
        warehouse = data.get('warehouse')

        if not warehouse['liquids']:
            embed.add_field(name=_('Liquids'), value="```n/a```", inline=False)
        elif data['unlimited']['liquids']:
            embed.add_field(name=_('Liquids'), value="```" + _('unlimited') + "```", inline=False)
        else:
            length = max(len(x) for x in LIQUIDS.values()) + 1
            embed.add_field(name=_('Liquids'),
                            value="```" + "\n".join([
                                f"{LIQUIDS[int(k)]}:{' ' * (length - len(LIQUIDS[int(k)]))}{v / 1000:.2f} tons"
                                for k, v in warehouse['liquids'].items()
                            ]) + "```",
                            inline=False)

    @staticmethod
    def render_weapons(embed: discord.Embed, data: dict):
        warehouse = data.get('warehouse')

        if data['unlimited']['weapon']:
            embed.add_field(name=_('Weapons'), value="```" + _("unlimited") + "```", inline=False)
            return

        raw_weapons: dict[str, int] = warehouse.get('weapon', {}) or {}
        if not raw_weapons:
            embed.add_field(name=_('Weapons'), value="```n/a```", inline=False)
            return

        # Convert to {qty: [weapon_name, ...]}
        weapons_by_qty: dict[int, list[str]] = {}
        for k, v in raw_weapons.items():
            weapons_by_qty.setdefault(v, []).append(k.split('.')[-1])

        if not weapons_by_qty:
            embed.add_field(name=_('Weapons'), value="```n/a```", inline=False)
            return

        if len(weapons_by_qty) == 1:
            embed.add_field(
                name=_('Weapons'),
                value="```" + _('{} items each').format(next(iter(weapons_by_qty))) + "```",
                inline=False
            )
            return

        # Use the most common quantity as default (mode), not max().
        qty_counts = Counter(raw_weapons.values())
        default_qty = max(qty_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]

        default_types: list[str] = []
        custom_by_qty: dict[int, list[str]] = {}
        for qty, names in weapons_by_qty.items():
            names.sort()
            if qty == default_qty:
                default_types.extend(names)
            else:
                custom_by_qty[qty] = names

        # If for some reason everything is "default", keep it compact.
        if not custom_by_qty:
            embed.add_field(
                name=_('Weapons'),
                value="```" + _('{} items each').format(default_qty) + "```",
                inline=False
            )
            return

        # Put the default summary FIRST so it won't get truncated away.
        lines: list[str] = []
        if default_types:
            lines.append(f"{default_qty:3d}: " + _("all other weapons") + f" ({len(default_types)} types)")

        # Then list custom quantities (show above-default early too).
        custom_keys = sorted(custom_by_qty.keys(), key=lambda q: (0 if q > default_qty else 1, q))
        for qty in custom_keys:
            lines.append(f"{qty:3d}: {', '.join(custom_by_qty[qty])}")

        # Respect Discord field limit (1024); we wrap in ```...```.
        budget = 1018
        rendered_lines: list[str] = []
        used = 0
        hidden_types = 0

        def count_types_in_line(s: str) -> int:
            if "(" in s and "types" in s:
                try:
                    return int(s.rsplit("(", 1)[-1].split()[0])
                except (ValueError, IndexError):
                    return 0
            if ":" in s:
                tail = s.split(":", 1)[1].strip()
                if not tail or tail.startswith(_("all other weapons")):
                    return 0
                return tail.count(",") + 1
            return 0

        for idx, line in enumerate(lines):
            extra = len(line) + (1 if rendered_lines else 0)
            if used + extra > budget:
                for rest in lines[idx:]:
                    hidden_types += count_types_in_line(rest)
                break
            rendered_lines.append(line)
            used += extra

        if hidden_types:
            marker = _("... and {} more types").format(hidden_types)
            extra = len(marker) + (1 if rendered_lines else 0)
            if used + extra <= budget:
                rendered_lines.append(marker)

        embed.add_field(
            name=_('Weapons'),
            value="```" + "\n".join(rendered_lines) + "```",
            inline=False
        )

    @staticmethod
    def render_aircraft(embed: discord.Embed, data: dict):
        warehouse = data.get('warehouse')

        if data['unlimited']['aircraft']:
            embed.add_field(name=_('Aircraft'), value="```" + _("unlimited") + "```", inline=False)
            return

        raw_aircraft: dict[str, int] = warehouse.get('aircraft', {}) or {}
        if not raw_aircraft:
            embed.add_field(name=_('Aircraft'), value="```n/a```", inline=False)
            return

        # Use the most common quantity as "default" (robust against one-offs above/below).
        qty_counts = Counter(raw_aircraft.values())
        default_qty = max(qty_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]

        # Split into "default" and "custom" quantities
        default_types: list[str] = []
        custom_by_qty: dict[int, list[str]] = {}
        for a_type, qty in raw_aircraft.items():
            if qty == default_qty:
                default_types.append(a_type)
            else:
                custom_by_qty.setdefault(qty, []).append(a_type)

        # Nothing custom => show one compact line
        if not custom_by_qty:
            embed.add_field(
                name=_('Aircraft'),
                value="```" + _('{} items each').format(default_qty) + "```",
                inline=False
            )
            return

        for qty in custom_by_qty:
            custom_by_qty[qty].sort()

        # Put the default summary FIRST so it doesn't get truncated away.
        lines: list[str] = []
        if default_types:
            lines.append(f"{default_qty:3d}: " + _("all other aircraft") + f" ({len(default_types)} types)")

        # Then list custom quantities (show "above default" early too).
        custom_keys = sorted(custom_by_qty.keys(), key=lambda q: (0 if q > default_qty else 1, q))
        for qty in custom_keys:
            lines.append(f"{qty:3d}: {', '.join(custom_by_qty[qty])}")

        # Keep within Discord's field value limit (1024). We wrap in ```...```, so budget a bit.
        budget = 1018
        rendered_lines: list[str] = []
        used = 0
        hidden_types = 0

        for idx, line in enumerate(lines):
            extra = len(line) + (1 if rendered_lines else 0)
            if used + extra > budget:
                # Count how many types are hidden in the remaining lines (rough but useful).
                def count_types_in_line(s: str) -> int:
                    if "(" in s and "types" in s:
                        try:
                            return int(s.rsplit("(", 1)[-1].split()[0])
                        except (ValueError, IndexError):
                            return 0
                    if ":" in s:
                        tail = s.split(":", 1)[1].strip()
                        if not tail or tail.startswith(_("all other aircraft")):
                            return 0
                        return tail.count(",") + 1
                    return 0

                for rest in lines[idx:]:
                    hidden_types += count_types_in_line(rest)
                break

            rendered_lines.append(line)
            used += extra

        if hidden_types:
            marker = _("... and {} more types").format(hidden_types)
            extra = len(marker) + (1 if rendered_lines else 0)
            if used + extra <= budget:
                rendered_lines.append(marker)

        embed.add_field(
            name=_('Aircraft'),
            value="```" + "\n".join(rendered_lines) + "```",
            inline=False
        )

    async def render(self, interaction: discord.Interaction, server: Server, airbase: dict, data: dict):
        if 'code' in airbase:
            self.add_field(name=_('Code'), value=airbase['code'] or 'n/a')
        else:
            self.add_field(name=_('Type'), value=airbase['type'])
        self.add_field(name=_('Coalition'), value=Side(data['coalition']).name.title())
        self.add_field(name='_ _', value='_ _')
        self.add_field(name=_('Dynamic Spawns'),
                       value=_('available') if airbase['dynamic']['dynamicSpawnAvailable'] else 'unavailable')
        self.add_field(name=_('Dynamic Hotstarts'),
                       value=_('available') if airbase['dynamic']['allowHotSpawn'] else 'unavailable')
        self.add_field(name='_ _', value='_ _')
        await report.Ruler(self.env).render(header=_('Position'))
        d, m, s, f = utils.dd_to_dms(airbase['lat'])
        lat = ('N' if d > 0 else 'S') + '{:02d}°{:02d}\'{:02d}"'.format(int(abs(d)), int(abs(m)), int(abs(s)))
        d, m, s, f = utils.dd_to_dms(airbase['lng'])
        lng = ('E' if d > 0 else 'W') + '{:03d}°{:02d}\'{:02d}"'.format(int(abs(d)), int(abs(m)), int(abs(s)))
        self.add_field(name='Position', value=f"{lat}\n{lng}\n{airbase['mgrs']}")
        alt = int(airbase['alt'] * const.METER_IN_FEET)
        self.add_field(name='Altitude', value='{} ft'.format(alt))
        self.add_field(name='MagVar', value=f"{airbase['magVar']:.2f}")
        runways = data.get('runways')
        if runways:
            name = []
            course = []
            length = []
            for runway in runways:
                name.append(runway['Name'])
                course.append(runway['course'])
                length.append(int(runway['length'] * const.METER_IN_FEET))
            if name:
                await report.Ruler(self.env).render(header=_('Runways'))
                self.add_field(name=_('Runway'), value='\n'.join(f"{x:02d}" for x in name))
                self.add_field(name=_('Course'), value='\n'.join(f"{utils.rad_to_heading(x):.0f}°" for x in course))
                self.add_field(name=_('Length (ft)'), value='\n'.join(f"{x} ft" for x in length))

        parking = data.get('parking')
        if parking:
            spot_count: dict[int, int] = defaultdict(int)
            for parking_spot in parking:
                spot_count[parking_spot['Term_Type']] += 1

            spot_names: dict[int, str] = {
                16: _('Runway spawns'),
                40: _('Helicopter only spawns'),
                68: _('Hardened air shelter'),
                72: _('Open/Shelter airplane only'),
                100: _('Small shelter'),
                104: _('Open air spawn')
            }

            await report.Ruler(self.env).render(header=_('Parking'))
            self.add_field(name=_('Type'), value='\n'.join(f"{spot_names[spot_type]}" for spot_type in spot_count.keys()))
            self.add_field(name=_('Count'), value='\n'.join(f"{count}" for count in spot_count.values()))
            self.add_field(name='_ _', value='_ _')

        sides = utils.get_sides(interaction.client, interaction, server)
        if ((data['coalition'] in [0, 2] and Coalition.BLUE in sides) or
                (data['coalition'] in [0, 1] and Coalition.RED in sides)):
            warehouse = data.get('warehouse')
            if warehouse:
                await report.Ruler(self.env).render(header=_('Warehouse'))

                Info.render_liquids(self.embed, data)
                Info.render_weapons(self.embed, data)
                Info.render_aircraft(self.embed, data)
