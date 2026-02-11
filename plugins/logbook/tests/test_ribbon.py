"""
Test suite for the ribbon generation utilities.

Tests RibbonGenerator and create_ribbon_rack with various inputs including:
- Valid colors
- Invalid colors
- Edge cases (empty lists, large lists, etc.)
- PNG output validation

Run from project root:
    python plugins/logbook/tests/test_ribbon.py
Or:
    python -m plugins.logbook.tests.test_ribbon
"""
import sys
import os

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Also add the utils directory for direct import
utils_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if utils_dir not in sys.path:
    sys.path.insert(0, utils_dir)

try:
    from plugins.logbook.utils.ribbon import RibbonGenerator, create_ribbon_rack, HAS_IMAGING, DEFAULT_RIBBON_COLORS
except ImportError:
    # Try direct import when running from tests directory
    from utils.ribbon import RibbonGenerator, create_ribbon_rack, HAS_IMAGING, DEFAULT_RIBBON_COLORS


def is_valid_png(data: bytes) -> bool:
    """Check if data starts with PNG magic bytes."""
    PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
    return data is not None and data[:8] == PNG_MAGIC


def test_ribbon_generator_basic():
    """Test basic RibbonGenerator functionality."""
    print("\n=== Test: RibbonGenerator Basic ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    generator = RibbonGenerator("Test Award")
    result = generator.generate()

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Generated {len(result)} bytes of PNG data")
    return True


def test_ribbon_generator_explicit_colors():
    """Test RibbonGenerator with explicit colors."""
    print("\n=== Test: RibbonGenerator Explicit Colors ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    colors = ["#FF0000", "#FFFFFF", "#0000FF"]  # Red, White, Blue
    generator = RibbonGenerator("Flag Ribbon", colors=colors)
    result = generator.generate(explicit_colors=True)

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Generated explicit color ribbon ({len(result)} bytes)")
    return True


def test_ribbon_generator_invalid_colors():
    """Test RibbonGenerator with invalid color formats."""
    print("\n=== Test: RibbonGenerator Invalid Colors ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    # Test various invalid color formats
    invalid_colors = [
        "not_a_color",
        "#GGGGGG",
        "12345",
        "",
        "#12",  # Too short
    ]

    generator = RibbonGenerator("Invalid Color Test", colors=invalid_colors)
    result = generator.generate(explicit_colors=True)

    if result is None:
        print("FAIL: generate() returned None for invalid colors (should fallback to gray)")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Handled invalid colors gracefully ({len(result)} bytes)")
    return True


def test_ribbon_generator_mixed_colors():
    """Test RibbonGenerator with mix of valid and invalid colors."""
    print("\n=== Test: RibbonGenerator Mixed Valid/Invalid Colors ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    colors = ["#FF0000", "invalid", "#00FF00", "also_invalid", "#0000FF"]
    generator = RibbonGenerator("Mixed Colors", colors=colors)
    result = generator.generate(explicit_colors=True)

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Handled mixed colors ({len(result)} bytes)")
    return True


def test_ribbon_generator_single_color():
    """Test RibbonGenerator with a single color."""
    print("\n=== Test: RibbonGenerator Single Color ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    generator = RibbonGenerator("Single Color", colors=["#800080"])  # Purple
    result = generator.generate(explicit_colors=True)

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Generated single color ribbon ({len(result)} bytes)")
    return True


def test_ribbon_generator_many_colors():
    """Test RibbonGenerator with many colors."""
    print("\n=== Test: RibbonGenerator Many Colors ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    # 20 colors
    colors = [f"#{i:02x}{(i*5)%256:02x}{(i*10)%256:02x}" for i in range(0, 200, 10)]
    generator = RibbonGenerator("Many Colors", colors=colors)
    result = generator.generate(explicit_colors=True)

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Generated ribbon with {len(colors)} colors ({len(result)} bytes)")
    return True


def test_ribbon_generator_custom_dimensions():
    """Test RibbonGenerator with custom width/height."""
    print("\n=== Test: RibbonGenerator Custom Dimensions ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    # Small ribbon
    gen_small = RibbonGenerator("Small", width=50, height=20)
    result_small = gen_small.generate()

    # Large ribbon
    gen_large = RibbonGenerator("Large", width=500, height=200)
    result_large = gen_large.generate()

    if result_small is None or result_large is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result_small) or not is_valid_png(result_large):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Small ribbon: {len(result_small)} bytes, Large ribbon: {len(result_large)} bytes")
    return True


def test_ribbon_generator_deterministic():
    """Test that same name produces same ribbon."""
    print("\n=== Test: RibbonGenerator Deterministic Output ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    gen1 = RibbonGenerator("Deterministic Test")
    gen2 = RibbonGenerator("Deterministic Test")

    result1 = gen1.generate()
    result2 = gen2.generate()

    if result1 is None or result2 is None:
        print("FAIL: generate() returned None")
        return False

    if result1 != result2:
        print("FAIL: Same name produced different ribbons")
        return False

    print("PASS: Same name produces identical ribbons")
    return True


def test_ribbon_generator_different_names():
    """Test that different names produce different ribbons."""
    print("\n=== Test: RibbonGenerator Different Names ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    gen1 = RibbonGenerator("Award A")
    gen2 = RibbonGenerator("Award B")

    result1 = gen1.generate()
    result2 = gen2.generate()

    if result1 is None or result2 is None:
        print("FAIL: generate() returned None")
        return False

    if result1 == result2:
        print("FAIL: Different names produced identical ribbons")
        return False

    print("PASS: Different names produce different ribbons")
    return True


def test_create_ribbon_rack_basic():
    """Test basic create_ribbon_rack functionality."""
    print("\n=== Test: create_ribbon_rack Basic ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [
        ("Distinguished Flying Cross", ["#4169E1", "#FFFFFF", "#4169E1"], 1),
        ("Air Medal", ["#FFD700", "#00008B", "#FFD700"], 2),
        ("Bronze Star", ["#CD7F32", "#FFFFFF", "#CD7F32"], 1),
    ]

    result = create_ribbon_rack(awards)

    if result is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Created ribbon rack ({len(result)} bytes)")
    return True


def test_create_ribbon_rack_empty():
    """Test create_ribbon_rack with empty awards list."""
    print("\n=== Test: create_ribbon_rack Empty List ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    result = create_ribbon_rack([])

    if result is not None:
        print("FAIL: Expected None for empty awards list")
        return False

    print("PASS: Correctly returned None for empty list")
    return True


def test_create_ribbon_rack_single_award():
    """Test create_ribbon_rack with a single award."""
    print("\n=== Test: create_ribbon_rack Single Award ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [("Single Award", ["#FF0000", "#FFFFFF", "#0000FF"], 1)]
    result = create_ribbon_rack(awards)

    if result is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Created single award rack ({len(result)} bytes)")
    return True


def test_create_ribbon_rack_large():
    """Test create_ribbon_rack with many awards."""
    print("\n=== Test: create_ribbon_rack Large (50 awards) ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [(f"Award {i}", None, 1) for i in range(50)]
    result = create_ribbon_rack(awards)

    if result is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Created rack with 50 awards ({len(result)} bytes)")
    return True


def test_create_ribbon_rack_very_large():
    """Test create_ribbon_rack with very large number of awards."""
    print("\n=== Test: create_ribbon_rack Very Large (200 awards) ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [(f"Award {i}", None, 1) for i in range(200)]
    result = create_ribbon_rack(awards)

    if result is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Created rack with 200 awards ({len(result)} bytes)")
    return True


def test_create_ribbon_rack_custom_rows():
    """Test create_ribbon_rack with custom ribbons_per_row."""
    print("\n=== Test: create_ribbon_rack Custom Rows ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [(f"Award {i}", None, 1) for i in range(10)]

    # Test with different ribbons per row
    result_2 = create_ribbon_rack(awards, ribbons_per_row=2)
    result_5 = create_ribbon_rack(awards, ribbons_per_row=5)

    if result_2 is None or result_5 is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result_2) or not is_valid_png(result_5):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: 2 per row: {len(result_2)} bytes, 5 per row: {len(result_5)} bytes")
    return True


def test_create_ribbon_rack_scale():
    """Test create_ribbon_rack with different scales."""
    print("\n=== Test: create_ribbon_rack Scale ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [("Test Award", None, 1)]

    result_half = create_ribbon_rack(awards, scale=0.5)
    result_double = create_ribbon_rack(awards, scale=2.0)

    if result_half is None or result_double is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result_half) or not is_valid_png(result_double):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Half scale: {len(result_half)} bytes, Double scale: {len(result_double)} bytes")
    return True


def test_create_ribbon_rack_multiple_counts():
    """Test create_ribbon_rack with awards having multiple counts."""
    print("\n=== Test: create_ribbon_rack Multiple Counts ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [
        ("Award A", ["#FF0000"], 3),  # 3 of this award
        ("Award B", ["#00FF00"], 2),  # 2 of this award
        ("Award C", ["#0000FF"], 1),  # 1 of this award
    ]

    result = create_ribbon_rack(awards)

    if result is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Created rack with 6 total ribbons ({len(result)} bytes)")
    return True


def test_create_ribbon_rack_none_colors():
    """Test create_ribbon_rack with None colors (auto-generated)."""
    print("\n=== Test: create_ribbon_rack None Colors ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [
        ("Auto Award 1", None, 1),
        ("Auto Award 2", None, 1),
        ("Auto Award 3", None, 1),
    ]

    result = create_ribbon_rack(awards)

    if result is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Created rack with auto-generated colors ({len(result)} bytes)")
    return True


def test_create_ribbon_rack_mixed_colors():
    """Test create_ribbon_rack with mix of specified and None colors."""
    print("\n=== Test: create_ribbon_rack Mixed Colors ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [
        ("Explicit Colors", ["#FF0000", "#FFFFFF", "#0000FF"], 1),
        ("Auto Colors", None, 1),
        ("More Explicit", ["#FFD700", "#000000"], 1),
    ]

    result = create_ribbon_rack(awards)

    if result is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Created rack with mixed color types ({len(result)} bytes)")
    return True


def test_create_ribbon_rack_zero_count():
    """Test create_ribbon_rack with zero count awards."""
    print("\n=== Test: create_ribbon_rack Zero Count ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    awards = [
        ("Zero Count", ["#FF0000"], 0),  # Should not appear
        ("One Count", ["#00FF00"], 1),   # Should appear
    ]

    result = create_ribbon_rack(awards)

    if result is None:
        print("FAIL: create_ribbon_rack() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Handled zero count ({len(result)} bytes)")
    return True


def test_color_name_support():
    """Test that named colors work (if matplotlib supports them)."""
    print("\n=== Test: Color Name Support ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    colors = ["red", "white", "blue", "gold", "navy"]
    generator = RibbonGenerator("Named Colors", colors=colors)
    result = generator.generate(explicit_colors=True)

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Named colors work ({len(result)} bytes)")
    return True


def test_rgb_tuple_string():
    """Test RGB tuple-like strings (should fail gracefully)."""
    print("\n=== Test: RGB Tuple String Edge Case ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    colors = ["(255, 0, 0)", "(0, 255, 0)"]  # Invalid format
    generator = RibbonGenerator("Tuple Strings", colors=colors)
    result = generator.generate(explicit_colors=True)

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Handled tuple-like strings gracefully ({len(result)} bytes)")
    return True


def test_empty_string_name():
    """Test RibbonGenerator with empty string name."""
    print("\n=== Test: Empty String Name ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    generator = RibbonGenerator("")
    result = generator.generate()

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Empty name handled ({len(result)} bytes)")
    return True


def test_unicode_name():
    """Test RibbonGenerator with unicode characters in name."""
    print("\n=== Test: Unicode Name ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    generator = RibbonGenerator("Medaille d'honneur")
    result = generator.generate()

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Unicode name handled ({len(result)} bytes)")
    return True


def test_emoji_name():
    """Test RibbonGenerator with emoji in name."""
    print("\n=== Test: Emoji Name ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    generator = RibbonGenerator("Top Gun Award")
    result = generator.generate()

    if result is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Emoji name handled ({len(result)} bytes)")
    return True


def test_stripe_percentage_bounds():
    """Test RibbonGenerator with extreme stripe percentage bounds."""
    print("\n=== Test: Stripe Percentage Bounds ===")

    if not HAS_IMAGING:
        print("SKIP: PIL/numpy not installed")
        return None

    # Very narrow stripes
    gen_narrow = RibbonGenerator("Narrow", min_stripe_percent=1, max_stripe_percent=5)
    result_narrow = gen_narrow.generate()

    # Very wide stripes
    gen_wide = RibbonGenerator("Wide", min_stripe_percent=30, max_stripe_percent=50)
    result_wide = gen_wide.generate()

    if result_narrow is None or result_wide is None:
        print("FAIL: generate() returned None")
        return False

    if not is_valid_png(result_narrow) or not is_valid_png(result_wide):
        print("FAIL: Output is not valid PNG data")
        return False

    print(f"PASS: Narrow: {len(result_narrow)} bytes, Wide: {len(result_wide)} bytes")
    return True


def run_all_tests():
    """Run all tests and report results."""
    print("=" * 60)
    print("Ribbon Generation Test Suite")
    print("=" * 60)

    if not HAS_IMAGING:
        print("\nWARNING: PIL/numpy/matplotlib not installed!")
        print("All tests will be skipped.")
        print("\nTo install required packages:")
        print("  pip install pillow numpy matplotlib")
        print("=" * 60)
        return False

    print(f"\nUsing default colors: {len(DEFAULT_RIBBON_COLORS)} colors defined")

    tests = [
        test_ribbon_generator_basic,
        test_ribbon_generator_explicit_colors,
        test_ribbon_generator_invalid_colors,
        test_ribbon_generator_mixed_colors,
        test_ribbon_generator_single_color,
        test_ribbon_generator_many_colors,
        test_ribbon_generator_custom_dimensions,
        test_ribbon_generator_deterministic,
        test_ribbon_generator_different_names,
        test_create_ribbon_rack_basic,
        test_create_ribbon_rack_empty,
        test_create_ribbon_rack_single_award,
        test_create_ribbon_rack_large,
        test_create_ribbon_rack_very_large,
        test_create_ribbon_rack_custom_rows,
        test_create_ribbon_rack_scale,
        test_create_ribbon_rack_multiple_counts,
        test_create_ribbon_rack_none_colors,
        test_create_ribbon_rack_mixed_colors,
        test_create_ribbon_rack_zero_count,
        test_color_name_support,
        test_rgb_tuple_string,
        test_empty_string_name,
        test_unicode_name,
        test_emoji_name,
        test_stripe_percentage_bounds,
    ]

    passed = 0
    failed = 0
    skipped = 0

    for test in tests:
        try:
            result = test()
            if result:
                passed += 1
            elif result is False:
                failed += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"ERROR in {test.__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
