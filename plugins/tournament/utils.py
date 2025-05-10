from io import BytesIO

import aiohttp
from PIL import Image, ImageDraw, ImageFont


def create_tournament_matches(squadrons: list[tuple[int, float]]) -> list[tuple[int, int]]:
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
