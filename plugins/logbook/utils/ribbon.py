"""
Ribbon image generation utility for the Logbook plugin.
Generates military-style award ribbons either from specified colors or auto-generated patterns.
"""
import hashlib
import io
import random
from typing import Optional

try:
    from PIL import Image, ImageOps
    import numpy as np
    from matplotlib.colors import to_rgb
    HAS_IMAGING = True
except ImportError:
    HAS_IMAGING = False


# Default colors for auto-generated ribbons
DEFAULT_RIBBON_COLORS = [
    "#C0C0C0",  # Silver
    "#B9CCED",  # Light Steel Blue
    "#800000",  # Maroon
    "#008000",  # Green
    "#000B40",  # Navy
    "#DD1C1A",  # Red
    "#FFB20F",  # Gold
    "#5A5F6F",  # Steel Blue
    "#514B23",  # Olive Drab
    "#FFFFFF",  # White
    "#000000",  # Black
]


class RibbonGenerator:
    """Generates military-style ribbon images from award names or specified colors."""

    def __init__(
        self,
        name: str,
        colors: Optional[list[str]] = None,
        width: int = 190,
        height: int = 64,
        min_stripe_percent: int = 5,
        max_stripe_percent: int = 40
    ):
        """
        Initialize the ribbon generator.

        Args:
            name: The award name (used for hash-based generation if no colors specified)
            colors: Optional list of hex color strings for the ribbon stripes
            width: Width of the ribbon image in pixels
            height: Height of the ribbon image in pixels
            min_stripe_percent: Minimum stripe width as percentage of ribbon width
            max_stripe_percent: Maximum stripe width as percentage of ribbon width
        """
        self.name = name
        self.colors = colors or DEFAULT_RIBBON_COLORS
        self.width = width
        self.height = height
        self.min_stripe_width = int((min_stripe_percent / 100) * width)
        self.max_stripe_width = int((max_stripe_percent / 100) * width)
        self._hash = hashlib.sha256(name.encode()).hexdigest()

    def _get_stripe_pattern(self) -> list[tuple[str, int]]:
        """
        Generate a stripe pattern based on the award name hash.
        Returns list of (color, width) tuples.
        """
        # Seed random with the hash for consistent generation
        hash_int = int(self._hash, 16)
        rng = random.Random(hash_int)

        # Determine number of stripes (2-14)
        num_stripes = rng.randint(2, 14)

        # Generate stripes
        stripe_colors = rng.choices(self.colors, k=num_stripes)

        remaining_width = self.width
        widths = []

        for i in range(num_stripes):
            if remaining_width < self.min_stripe_width:
                widths.append(remaining_width)
                break

            if i == num_stripes - 1:
                widths.append(remaining_width)
                break

            max_width = min(self.max_stripe_width, remaining_width)
            width = rng.randint(self.min_stripe_width, max(self.min_stripe_width, max_width))
            widths.append(width)
            remaining_width -= width

        return list(zip(stripe_colors, widths))

    def _get_explicit_stripe_pattern(self) -> list[tuple[str, int]]:
        """
        Generate stripes from explicitly specified colors (equal width stripes).
        """
        if not self.colors:
            return self._get_stripe_pattern()

        stripe_width = self.width // len(self.colors)
        remaining = self.width - (stripe_width * len(self.colors))

        pattern = []
        for i, color in enumerate(self.colors):
            # Add any remaining pixels to the last stripe
            width = stripe_width + (remaining if i == len(self.colors) - 1 else 0)
            pattern.append((color, width))

        return pattern

    def generate(self, explicit_colors: bool = False) -> Optional[bytes]:
        """
        Generate the ribbon image.

        Args:
            explicit_colors: If True, use specified colors as equal-width stripes.
                           If False, generate pattern from hash.

        Returns:
            PNG image bytes, or None if imaging libraries not available.
        """
        if not HAS_IMAGING:
            return None

        if explicit_colors and self.colors:
            pattern = self._get_explicit_stripe_pattern()
        else:
            pattern = self._get_stripe_pattern()

        # Create the ribbon array
        img_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        current_x = 0
        for color, width in pattern:
            end_x = min(current_x + width, self.width)
            try:
                rgb = np.array(to_rgb(color)) * 255
            except ValueError:
                rgb = np.array([128, 128, 128])  # Default gray for invalid colors
            img_array[:, current_x:end_x] = rgb
            current_x = end_x

        # Create symmetrical pattern by mirroring
        img_array = np.concatenate((np.flip(img_array, axis=1), img_array), axis=1)

        # Crop to desired width (center portion)
        start = (img_array.shape[1] - self.width) // 2
        img_array = img_array[:, start:start + self.width]

        # Convert to PIL Image
        img = Image.fromarray(img_array.astype('uint8'), 'RGB')

        # Save to bytes
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        return buffer.getvalue()


def create_ribbon_rack(
    awards: list[tuple[str, Optional[list[str]], int]],
    ribbons_per_row: int = 3,
    spacing: int = 5,
    border: int = 2,
    scale: float = 1.0
) -> Optional[bytes]:
    """
    Create a ribbon rack (quilt) from multiple awards.

    Args:
        awards: List of (award_name, ribbon_colors, count) tuples
        ribbons_per_row: Maximum ribbons per row
        spacing: Spacing between ribbons in pixels
        border: Border width around each ribbon
        scale: Scale factor for the final image

    Returns:
        PNG image bytes, or None if imaging libraries not available.
    """
    if not HAS_IMAGING:
        return None

    # Generate all ribbon images
    ribbons = []
    for name, colors, count in awards:
        generator = RibbonGenerator(name, colors=colors)
        ribbon_bytes = generator.generate(explicit_colors=bool(colors))
        if ribbon_bytes:
            ribbon_img = Image.open(io.BytesIO(ribbon_bytes))
            # Add border
            if border > 0:
                ribbon_img = ImageOps.expand(ribbon_img, border=border, fill='black')
            # Add ribbon multiple times if count > 1 (for multiple awards of same type)
            for _ in range(count):
                ribbons.append(ribbon_img)

    if not ribbons:
        return None

    # Calculate dimensions
    num_ribbons = len(ribbons)
    ribbon_width, ribbon_height = ribbons[0].size

    row_width = min(ribbons_per_row, num_ribbons)
    num_rows = (num_ribbons + ribbons_per_row - 1) // ribbons_per_row
    quilt_width = row_width * ribbon_width + (row_width - 1) * spacing
    quilt_height = num_rows * ribbon_height + (num_rows - 1) * spacing

    # Create transparent background
    quilt = Image.new('RGBA', (quilt_width, quilt_height), (0, 0, 0, 0))

    # Calculate top row ribbon count (for centering partial row)
    top_row_ribbons = num_ribbons % ribbons_per_row or ribbons_per_row

    # Arrange ribbons
    ribbon_idx = 0
    for row in range(num_rows):
        ribbons_in_row = top_row_ribbons if row == 0 and num_ribbons % ribbons_per_row else ribbons_per_row
        if row == 0 and num_ribbons % ribbons_per_row:
            ribbons_in_row = num_ribbons % ribbons_per_row

        # Center the row
        row_actual_width = ribbons_in_row * ribbon_width + (ribbons_in_row - 1) * spacing
        offset = (quilt_width - row_actual_width) // 2

        for col in range(ribbons_in_row):
            if ribbon_idx >= len(ribbons):
                break
            x = offset + col * (ribbon_width + spacing)
            y = row * (ribbon_height + spacing)
            quilt.paste(ribbons[ribbon_idx], (x, y))
            ribbon_idx += 1

    # Apply scale
    if scale != 1.0:
        new_size = (int(quilt.width * scale), int(quilt.height * scale))
        quilt = quilt.resize(new_size, Image.Resampling.LANCZOS)

    # Save to bytes
    buffer = io.BytesIO()
    quilt.save(buffer, format='PNG')
    return buffer.getvalue()
