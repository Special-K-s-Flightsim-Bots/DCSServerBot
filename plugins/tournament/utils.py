import aiohttp
import math
import pandas as pd
import random

from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Border, Side, Font
from trueskill import Rating
from PIL import Image, ImageDraw, ImageFont


def create_elimination_matches(squadrons: list[tuple[int, float]]) -> list[tuple[int, int]]:
    """
    Create tournament matches using snake pairing system.

    Args:
        squadrons: List of tuples (squadron_id, trueskill_rating)

    Returns:
        List of tuples (squadron1_id, squadron2_id) representing matches
    """
    # Sort squadrons by TrueSkill rating in descending order
    sorted_squadrons = sorted(squadrons, key=lambda x: x[1], reverse=True)

    # Check if we have an even number of squadrons
    if len(sorted_squadrons) % 2 != 0:
        raise ValueError("Need an even number of squadrons for the tournament")

    matches = []
    n = len(sorted_squadrons)
    half = n // 2

    # Create matches using snake pairing
    for i in range(half):
        squad1 = sorted_squadrons[i][0]  # Get squadron_id from tuple
        squad2 = sorted_squadrons[-(i + 1)][0]  # Get opponent from bottom, moving upwards
        matches.append((squad1, squad2))

    return matches


def create_groups(squadrons: list[tuple[int, float]], num_groups: int) -> list[list[int]]:
    """
    Create random groups for the group phase of the tournament.

    Args:
        squadrons: List of squadron IDs
        num_groups: Number of groups to create

    Returns:
        List of groups, where each group is a list of squadron IDs

    Raises:
        ValueError: If number of groups is invalid or if there aren't enough squadrons
    """
    if num_groups <= 0:
        raise ValueError("Number of groups must be positive")

    if len(squadrons) < num_groups * 2:
        raise ValueError(f"Need at least {num_groups * 2} squadrons for {num_groups} groups (minimum 2 per group)")

    # Create a copy of the squadron list to shuffle
    squadrons_copy = [x[0] for x in squadrons]
    random.shuffle(squadrons_copy)

    # Calculate minimum squadrons per group
    min_per_group = len(squadrons_copy) // num_groups
    # Calculate how many groups get an extra squadron (if uneven division)
    extras = len(squadrons_copy) % num_groups

    groups = []
    current_idx = 0

    for group_num in range(num_groups):
        # Calculate the size for this group
        group_size = min_per_group + (1 if group_num < extras else 0)
        # Create the group
        group = squadrons_copy[current_idx:current_idx + group_size]
        groups.append(group)
        current_idx += group_size

    return groups


def create_group_matches(groups: list[list[int]]) -> list[tuple[int, int]]:
    """
    Create matches for group phase where each squadron plays against all other squadrons in their group.

    Args:
        groups: List of groups, where each group is a list of squadron IDs
               (output from create_balanced_groups)

    Returns:
        List of tuples (squadron1_id, squadron2_id) representing matches

    Example:
        For a group [1, 2, 3], it creates matches [(1,2), (1,3), (2,3)]
    """
    matches = []

    # For each group
    for group in groups:
        # Create matches between each pair of squadrons in the group
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                matches.append((group[i], group[j]))

    return matches


async def create_versus_image(team1_image_url: str, team2_image_url: str) -> BytesIO:
    timeout = aiohttp.ClientTimeout(total=30)  # 30 seconds total timeout
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Download first image
        async with session.get(team1_image_url) as response1:
            if not response1.headers.get('content-type', '').startswith('image/'):
                raise ValueError(f"URL does not point to an image: {team1_image_url}")
            img1_data = await response1.read()
            if len(img1_data) > 10 * 1024 * 1024:  # 10MB limit
                raise ValueError(f"Image too large: {team1_image_url}")

        # Download second image
        async with session.get(team2_image_url) as response2:
            if not response2.headers.get('content-type', '').startswith('image/'):
                raise ValueError(f"URL does not point to an image: {team2_image_url}")
            img2_data = await response2.read()
            if len(img2_data) > 10 * 1024 * 1024:  # 10MB limit
                raise ValueError(f"Image too large: {team2_image_url}")

    # Open images from binary data and convert to RGBA
    img1 = Image.open(BytesIO(img1_data)).convert('RGBA')
    img2 = Image.open(BytesIO(img2_data)).convert('RGBA')

    # Define standard size for each team image
    standard_size = (200, 200)

    # Resize images while maintaining aspect ratio
    img1.thumbnail(standard_size, Image.Resampling.LANCZOS)
    img2.thumbnail(standard_size, Image.Resampling.LANCZOS)

    # Add spacing for VS text
    spacing = 100
    total_width = img1.width + img2.width + spacing
    max_height = max(img1.height, img2.height)

    # Create new image with transparent background
    combined_image = Image.new('RGBA', (total_width, max_height), (255, 255, 255, 0))

    # Calculate vertical positions
    y1 = (max_height - img1.height) // 2
    y2 = (max_height - img2.height) // 2

    # Paste images
    combined_image.paste(img1, (0, y1), img1)
    combined_image.paste(img2, (img1.width + spacing, y2), img2)

    # Add VS text
    draw = ImageDraw.Draw(combined_image)
    try:
        font = ImageFont.truetype("arial.ttf", 32)
    except:
        font = ImageFont.load_default()

    vs_text = " vs "
    # Get text size
    text_bbox = draw.textbbox((0, 0), vs_text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]

    # Calculate text position
    x = (total_width - text_width) // 2
    y = (max_height - text_height) // 2

    # Draw text with outline for better visibility
    draw.text((x - 1, y - 1), vs_text, font=font, fill=(0, 0, 0, 255))
    draw.text((x + 1, y - 1), vs_text, font=font, fill=(0, 0, 0, 255))
    draw.text((x - 1, y + 1), vs_text, font=font, fill=(0, 0, 0, 255))
    draw.text((x + 1, y + 1), vs_text, font=font, fill=(0, 0, 0, 255))
    draw.text((x, y), vs_text, font=font, fill=(255, 255, 255, 255))

    # Save to binary buffer
    buffer = BytesIO()
    combined_image.save(buffer, format='PNG')
    buffer.seek(0)

    return buffer


def calculate_point_multipliers(killer_rating: Rating, victim_rating: Rating) -> tuple[float, float]:
    """
    Calculate point multipliers for both killer and victim based on pre-match TrueSkill ratings.

    Args:
        killer_rating (Rating): TrueSkill rating of the killer before the match
        victim_rating (Rating): TrueSkill rating of the victim before the match

    Returns:
        tuple[float, float]: (killer_multiplier, victim_multiplier)
    """
    killer_conservative_skill = killer_rating.mu - 3 * killer_rating.sigma
    victim_conservative_skill = victim_rating.mu - 3 * victim_rating.sigma

    skill_difference = victim_conservative_skill - killer_conservative_skill

    total_uncertainty = killer_rating.sigma + victim_rating.sigma
    uncertainty_factor = 1.0 / (1.0 + total_uncertainty / 8.33)

    # Add a small threshold for considering skills equal
    if abs(skill_difference) < 0.0001:
        # Skills are effectively equal - use base multiplier
        return 1.0, 1.0

    # Killer multiplier
    if skill_difference > 0:
        # Killer has lower skill than victim - they get bonus points
        killer_raw_multiplier = 1.0 + math.log(1 + skill_difference / 10) * 0.5
        killer_multiplier = min(killer_raw_multiplier * uncertainty_factor, 2.5)
    else:
        # Killer has higher skill - they get reduced points
        killer_raw_multiplier = 1.0 / (1 + abs(skill_difference / 20))
        killer_multiplier = max(killer_raw_multiplier * uncertainty_factor, 0.5)

    # Victim multiplier
    if skill_difference > 0:
        # Victim has higher skill - they lose fewer points
        victim_raw_multiplier = 1.0 / (1 + skill_difference / 20)
        victim_multiplier = max(victim_raw_multiplier * uncertainty_factor, 0.5)
    else:
        # Victim has lower skill - they lose more points
        victim_raw_multiplier = 1.0 + math.log(1 + abs(skill_difference) / 10) * 0.5
        victim_multiplier = min(victim_raw_multiplier * uncertainty_factor, 2.5)

    return killer_multiplier, victim_multiplier


def create_tournament_sheet(squadrons_df: pd.DataFrame, matches_df: pd.DataFrame, tournament_id: int):
    wb = Workbook()
    ws = wb.active
    ws.title = f"Tournament {tournament_id}"

    border = Border(left=Side(style='thin'),
                    right=Side(style='thin'),
                    top=Side(style='thin'),
                    bottom=Side(style='thin'))

    # Write group stages
    groups = squadrons_df.sort_values(['group_number', 'squadron_id'])

    # Start writing from row 2 to leave space for headers
    row = 2

    # Write group stage header
    ws.cell(row=1, column=1, value="Groups").font = Font(bold=True)

    # Write groups and squadrons
    for group_num, group_data in groups.groupby('group_number'):
        ws.cell(row=row, column=1, value=f'Group {group_num}').font = Font(bold=True)
        row += 1

        for _, squadron in group_data.iterrows():
            cell = ws.cell(row=row, column=1, value=squadron['name'])
            cell.border = border
            row += 1

        row += 1  # Space between groups

    # Create squadron_id to name mapping for easier lookup
    squadron_names = dict(zip(squadrons_df['squadron_id'], squadrons_df['name']))

    # Write matches by stage
    matches_sorted = matches_df.sort_values(['stage', 'match_id'])
    max_row = row  # Remember the maximum row used by groups

    # Group matches by stage
    for stage, stage_matches in matches_sorted.groupby('stage'):
        if stage == 1:
            # Group stage matches - place them next to the groups
            column = 3  # Start group matches from column C
            ws.cell(row=1, column=column, value="Group Stage Matches").font = Font(bold=True)

            row = 2
            for _, match in stage_matches.iterrows():
                red_name = squadron_names[match['squadron_red']]
                blue_name = squadron_names[match['squadron_blue']]

                ws.cell(row=row, column=column,
                        value=f'{red_name} vs {blue_name}').border = border
                ws.cell(row=row + 1, column=column,
                        value=f'Result: {match["squadron_red_rounds_won"]} - {match["squadron_blue_rounds_won"]}').border = border

                if match['winner_squadron_id']:
                    winner_name = squadron_names[match['winner_squadron_id']]
                    ws.cell(row=row + 2, column=column,
                            value=f'Winner: {winner_name}').font = Font(italic=True)

                row += 4  # Space for next match
        else:
            # Elimination stages - place them progressively to the right
            column = 3 + (stage - 1) * 3  # Each stage moves 3 columns to the right
            ws.cell(row=1, column=column, value=f"Stage {stage}").font = Font(bold=True)

            row_spacing = 4 * (2 ** (stage - 2))  # Increase spacing between matches for each stage
            base_row = 2

            for idx, match in enumerate(stage_matches.itertuples()):
                current_row = base_row + (idx * row_spacing)

                red_name = squadron_names[match.squadron_red]
                blue_name = squadron_names[match.squadron_blue]

                ws.cell(row=current_row, column=column,
                        value=f'{red_name} vs {blue_name}').border = border
                ws.cell(row=current_row + 1, column=column,
                        value=f'Result: {match.squadron_red_rounds_won} - {match.squadron_blue_rounds_won}').border = border

                if match.winner_squadron_id:
                    winner_name = squadron_names[match.winner_squadron_id]
                    ws.cell(row=current_row + 2, column=column,
                            value=f'Winner: {winner_name}').font = Font(italic=True)

    # Adjust column widths
    for col in ws.columns:
        max_length = 0
        for cell in col:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[col[0].column_letter].width = max_length + 2

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
