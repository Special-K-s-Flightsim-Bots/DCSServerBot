from core import report
from datetime import datetime

from .const import PRETENSE_RANKS


class Header(report.EmbedElement):
    async def render(self, data: dict):
        self.embed.description = f"Rankings as of <t:{int(datetime.now().timestamp())}:f>:"


class ZoneDistribution(report.PieChart):
    @staticmethod
    def calculate_zone_distribution(zones):
        blue_count = len(zones["blue"])
        neutral_count = len(zones["neutral"])
        red_count = len(zones["red"])
        total_count = blue_count + neutral_count + red_count

        blue_percentage = round(blue_count / total_count * 100, 1)
        neutral_percentage = round(neutral_count / total_count * 100, 1)
        red_percentage = round(red_count / total_count * 100, 1)

        return {
            "Blue": blue_percentage,
            "Neutral": neutral_percentage,
            "Red": red_percentage
        }

    async def render(self, data: dict):
        zone_distribution = self.calculate_zone_distribution(data["zones"])
        self.colors = ['blue', 'lightgrey', 'red']
        await super().render(zone_distribution)


class Top10Pilots(report.EmbedElement):
    @staticmethod
    def get_rank(xp):
        for rank in reversed(list(PRETENSE_RANKS.keys())):
            if xp >= PRETENSE_RANKS[rank]["requiredXP"]:
                return PRETENSE_RANKS[rank]["name"]
        return None

    async def render(self, data: dict):
        # Extract player scores from the JSON data
        player_scores = {}
        stats = data.get("stats", {})
        for player, stats in stats.items():
            if isinstance(stats, dict):  # Check if stats is a dictionary
                xp = stats.get("XP", 0)
                player_scores[player] = xp

        # Sort players by their score in descending order
        sorted_players = sorted(player_scores.items(), key=lambda x: x[1], reverse=True)

        # Add player scores to the leaderboard
        names = ''
        xp = ''
        ranks = ''
        for rank, (player, score) in enumerate(sorted_players[:10], start=1):
            names += f'{player}\n'
            xp += f'{score:>5}\n'
            ranks += f'{self.get_rank(score)}\n'
        self.embed.add_field(name='Name', value=names)
        self.embed.add_field(name='XP', value=xp)
        self.embed.add_field(name='Rank', value=ranks)
