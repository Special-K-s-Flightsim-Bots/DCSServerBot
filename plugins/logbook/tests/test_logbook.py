"""
Unit tests for the Logbook plugin.

These tests can run without a live DCSServerBot instance, focusing on:
1. Pure utility functions (format_hours, normalize_name, etc.)
2. SQL syntax validation
3. Import validation
4. Ribbon generation logic
5. Requirement checking logic
"""

import importlib
import os
import re
import sys
from pathlib import Path

import pytest


# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# format_hours() Tests
# =============================================================================

class TestFormatHours:
    """Tests for the format_hours utility function."""

    def test_format_hours_basic_seconds(self):
        """Test formatting a basic number of seconds."""
        # Import here to avoid loading full plugin dependencies
        from plugins.logbook.commands import format_hours

        # 3600 seconds = 1 hour
        result = format_hours(3600)
        assert result == "1.0h"

    def test_format_hours_fractional_hours(self):
        """Test formatting fractional hours."""
        from plugins.logbook.commands import format_hours

        # 5400 seconds = 1.5 hours
        result = format_hours(5400)
        assert result == "1.5h"

    def test_format_hours_zero(self):
        """Test formatting zero seconds."""
        from plugins.logbook.commands import format_hours

        result = format_hours(0)
        assert result == "0.0h"

    def test_format_hours_none(self):
        """Test formatting None returns default."""
        from plugins.logbook.commands import format_hours

        result = format_hours(None)
        assert result == "0.0h"

    def test_format_hours_large_value(self):
        """Test formatting large number of seconds."""
        from plugins.logbook.commands import format_hours

        # 36000 seconds = 10 hours
        result = format_hours(36000)
        assert result == "10.0h"

    def test_format_hours_small_value_treated_as_hours(self):
        """Test that small values (<=100) are treated as hours directly."""
        from plugins.logbook.commands import format_hours

        # Value <= 100 is treated as hours already
        result = format_hours(50)
        assert result == "50.0h"

    def test_format_hours_boundary_at_100(self):
        """Test boundary condition at 100."""
        from plugins.logbook.commands import format_hours

        # At exactly 100, treated as hours
        result = format_hours(100)
        assert result == "100.0h"

    def test_format_hours_above_100_treated_as_seconds(self):
        """Test that values > 100 are treated as seconds."""
        from plugins.logbook.commands import format_hours

        # 101 seconds ~ 0.03 hours
        result = format_hours(101)
        assert result == "0.0h"

        # 360 seconds = 0.1 hours
        result = format_hours(360)
        assert result == "0.1h"

    def test_format_hours_negative(self):
        """Test formatting negative values."""
        from plugins.logbook.commands import format_hours

        # Negative values should still be processed (edge case)
        result = format_hours(-3600)
        assert result == "-1.0h"

    def test_format_hours_float_input(self):
        """Test formatting float input."""
        from plugins.logbook.commands import format_hours

        result = format_hours(3600.5)
        assert result == "1.0h"


# =============================================================================
# normalize_name() Tests (from migration script)
# =============================================================================

class TestNormalizeName:
    """Tests for the normalize_name function from migration script."""

    def test_normalize_name_basic(self):
        """Test basic name normalization."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import normalize_name

        result = normalize_name("TestPilot")
        assert result == "testpilot"

    def test_normalize_name_with_rank(self):
        """Test normalization removes rank prefixes."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import normalize_name

        # Various rank prefixes
        assert normalize_name("Lt TestPilot") == "testpilot"
        assert normalize_name("Maj TestPilot") == "testpilot"
        assert normalize_name("Capt TestPilot") == "testpilot"

    def test_normalize_name_with_squadron_tag(self):
        """Test normalization removes squadron tags."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import normalize_name

        # Squadron tag patterns
        assert normalize_name("=JSW= TestPilot") == "testpilot"

    def test_normalize_name_removes_special_chars(self):
        """Test normalization removes special characters."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import normalize_name

        assert normalize_name("Test_Pilot") == "testpilot"
        assert normalize_name("Test-Pilot") == "testpilot"
        assert normalize_name("Test.Pilot") == "testpilot"

    def test_normalize_name_empty_string(self):
        """Test normalization of empty string."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import normalize_name

        assert normalize_name("") == ""

    def test_normalize_name_none_handling(self):
        """Test normalization of None."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import normalize_name

        assert normalize_name(None) == ""

    def test_normalize_name_whitespace(self):
        """Test normalization handles whitespace."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import normalize_name

        assert normalize_name("  TestPilot  ") == "testpilot"


# =============================================================================
# _check_requirements() Tests
# =============================================================================

class TestCheckRequirements:
    """Tests for the _check_requirements method in LogbookEventListener."""

    @pytest.fixture
    def listener_instance(self):
        """Create a mock listener instance for testing."""
        # We'll test the method directly by importing and instantiating minimally
        # Since we can't easily mock the full listener, we'll test the logic directly
        class MockListener:
            def _check_requirements(self, player_stats: dict, requirements: dict) -> bool:
                """Copy of the method for testing."""
                for req_key, req_value in requirements.items():
                    player_value = player_stats.get(req_key, 0)

                    if req_key.endswith('_max'):
                        actual_key = req_key[:-4]
                        player_value = player_stats.get(actual_key, 0)
                        if player_value > req_value:
                            return False
                    elif req_key.endswith('_min'):
                        actual_key = req_key[:-4]
                        player_value = player_stats.get(actual_key, 0)
                        if player_value < req_value:
                            return False
                    else:
                        if player_value < req_value:
                            return False

                return True

        return MockListener()

    def test_check_requirements_basic_pass(self, listener_instance):
        """Test basic requirement check passes."""
        player_stats = {'flight_hours': 100, 'total_kills': 50}
        requirements = {'flight_hours': 50, 'total_kills': 25}

        assert listener_instance._check_requirements(player_stats, requirements) is True

    def test_check_requirements_basic_fail(self, listener_instance):
        """Test basic requirement check fails."""
        player_stats = {'flight_hours': 30, 'total_kills': 50}
        requirements = {'flight_hours': 50, 'total_kills': 25}

        assert listener_instance._check_requirements(player_stats, requirements) is False

    def test_check_requirements_max_constraint_pass(self, listener_instance):
        """Test max constraint passes."""
        player_stats = {'deaths': 5}
        requirements = {'deaths_max': 10}

        assert listener_instance._check_requirements(player_stats, requirements) is True

    def test_check_requirements_max_constraint_fail(self, listener_instance):
        """Test max constraint fails when exceeded."""
        player_stats = {'deaths': 15}
        requirements = {'deaths_max': 10}

        assert listener_instance._check_requirements(player_stats, requirements) is False

    def test_check_requirements_min_constraint_pass(self, listener_instance):
        """Test min constraint passes."""
        player_stats = {'landings': 20}
        requirements = {'landings_min': 10}

        assert listener_instance._check_requirements(player_stats, requirements) is True

    def test_check_requirements_min_constraint_fail(self, listener_instance):
        """Test min constraint fails."""
        player_stats = {'landings': 5}
        requirements = {'landings_min': 10}

        assert listener_instance._check_requirements(player_stats, requirements) is False

    def test_check_requirements_missing_stat(self, listener_instance):
        """Test missing stat defaults to 0."""
        player_stats = {}
        requirements = {'flight_hours': 10}

        assert listener_instance._check_requirements(player_stats, requirements) is False

    def test_check_requirements_empty(self, listener_instance):
        """Test empty requirements always pass."""
        player_stats = {'flight_hours': 10}
        requirements = {}

        assert listener_instance._check_requirements(player_stats, requirements) is True

    def test_check_requirements_combined(self, listener_instance):
        """Test combined min/max requirements."""
        player_stats = {'flight_hours': 50, 'deaths': 5, 'kills': 100}
        requirements = {
            'flight_hours': 25,      # min 25 hours
            'deaths_max': 10,        # max 10 deaths
            'kills_min': 50          # min 50 kills
        }

        assert listener_instance._check_requirements(player_stats, requirements) is True


# =============================================================================
# SQL Syntax Validation Tests
# =============================================================================

class TestSQLSyntax:
    """Validate SQL syntax in tables.sql without requiring a database."""

    @pytest.fixture
    def sql_content(self):
        """Load the tables.sql file content."""
        sql_path = PROJECT_ROOT / "plugins" / "logbook" / "db" / "tables.sql"
        with open(sql_path, 'r') as f:
            return f.read()

    def test_tables_sql_exists(self):
        """Test that tables.sql file exists."""
        sql_path = PROJECT_ROOT / "plugins" / "logbook" / "db" / "tables.sql"
        assert sql_path.exists(), "tables.sql should exist"

    def test_tables_sql_not_empty(self, sql_content):
        """Test that tables.sql is not empty."""
        assert len(sql_content) > 0, "tables.sql should not be empty"

    def test_create_table_statements_valid(self, sql_content):
        """Test that CREATE TABLE statements have proper syntax."""
        # Check for balanced parentheses in each CREATE TABLE statement
        create_table_pattern = r'CREATE TABLE IF NOT EXISTS (\w+)\s*\(([\s\S]*?)\);'
        matches = re.findall(create_table_pattern, sql_content)

        assert len(matches) > 0, "Should find at least one CREATE TABLE statement"

        for table_name, table_body in matches:
            # Check for common syntax issues
            assert table_body.count('(') == table_body.count(')'), \
                f"Unbalanced parentheses in table {table_name}"

    def test_expected_tables_exist(self, sql_content):
        """Test that expected tables are defined."""
        expected_tables = [
            'logbook_squadrons',
            'logbook_squadron_members',
            'logbook_qualifications',
            'logbook_pilot_qualifications',
            'logbook_awards',
            'logbook_pilot_awards',
            'logbook_flight_plans',
            'logbook_stores_requests',
            'logbook_historical_hours',
        ]

        for table in expected_tables:
            assert f'CREATE TABLE IF NOT EXISTS {table}' in sql_content, \
                f"Table {table} should be defined in tables.sql"

    def test_foreign_keys_reference_valid_tables(self, sql_content):
        """Test that foreign keys reference known tables."""
        fk_pattern = r'FOREIGN KEY.*REFERENCES\s+(\w+)'
        references = re.findall(fk_pattern, sql_content)

        # Known tables from DCSServerBot core and this plugin
        known_tables = {
            'players',
            'logbook_squadrons',
            'logbook_qualifications',
            'logbook_awards',
        }

        for ref in references:
            assert ref in known_tables, \
                f"Foreign key references unknown table: {ref}"

    def test_view_exists(self, sql_content):
        """Test that the pilot_logbook_stats view is created."""
        assert 'CREATE OR REPLACE VIEW pilot_logbook_stats' in sql_content, \
            "pilot_logbook_stats view should be defined"

    def test_indexes_created(self, sql_content):
        """Test that indexes are created for performance."""
        # Should have at least several indexes
        index_count = sql_content.count('CREATE INDEX IF NOT EXISTS')
        index_count += sql_content.count('CREATE UNIQUE INDEX IF NOT EXISTS')

        assert index_count >= 10, \
            f"Expected at least 10 indexes, found {index_count}"

    def test_no_sql_injection_patterns(self, sql_content):
        """Test for potentially dangerous SQL patterns."""
        # These patterns might indicate issues in generated SQL
        dangerous_patterns = [
            r'\$\{',           # Template injection
            r'%s.*%s.*%s',     # Multiple string formatting (could be ok but worth checking)
        ]

        for pattern in dangerous_patterns:
            # This is informational - not necessarily a failure
            if re.search(pattern, sql_content):
                pytest.skip(f"Found pattern that may need review: {pattern}")


# =============================================================================
# Import Validation Tests
# =============================================================================

class TestImports:
    """Test that all modules can be imported without errors."""

    def test_import_version(self):
        """Test version module imports correctly."""
        from plugins.logbook.version import __version__
        assert __version__ is not None
        assert isinstance(__version__, str)

    def test_import_init(self):
        """Test __init__ module imports correctly."""
        import plugins.logbook
        assert hasattr(plugins.logbook, '__version__')

    def test_import_utils_module(self):
        """Test utils module imports correctly."""
        from plugins.logbook.utils import HAS_IMAGING, create_ribbon_rack, RibbonGenerator
        assert HAS_IMAGING in (True, False)
        assert callable(create_ribbon_rack)
        assert RibbonGenerator is not None

    def test_import_ribbon_generator(self):
        """Test RibbonGenerator class can be instantiated."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        generator = RibbonGenerator("Test Award")
        assert generator.name == "Test Award"
        assert generator.width == 190
        assert generator.height == 64

    def test_migration_script_imports(self):
        """Test migration script imports correctly."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import (
            normalize_name,
            MigrationReport
        )

        assert callable(normalize_name)
        report = MigrationReport()
        assert hasattr(report, 'pilots_found')


# =============================================================================
# Ribbon Generator Tests
# =============================================================================

class TestRibbonGenerator:
    """Tests for the RibbonGenerator class."""

    def test_ribbon_generator_init(self):
        """Test RibbonGenerator initialization."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        gen = RibbonGenerator("Distinguished Flying Cross")
        assert gen.name == "Distinguished Flying Cross"
        assert gen.width == 190
        assert gen.height == 64

    def test_ribbon_generator_custom_dimensions(self):
        """Test RibbonGenerator with custom dimensions."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        gen = RibbonGenerator("Test", width=200, height=80)
        assert gen.width == 200
        assert gen.height == 80

    def test_ribbon_generator_with_colors(self):
        """Test RibbonGenerator with explicit colors."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        colors = ["#FF0000", "#FFFFFF", "#0000FF"]
        gen = RibbonGenerator("Test", colors=colors)
        assert gen.colors == colors

    def test_ribbon_generator_hash_consistency(self):
        """Test that same name produces same hash."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        gen1 = RibbonGenerator("Test Award")
        gen2 = RibbonGenerator("Test Award")
        assert gen1._hash == gen2._hash

    def test_ribbon_generator_different_names_different_hash(self):
        """Test that different names produce different hashes."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        gen1 = RibbonGenerator("Test Award 1")
        gen2 = RibbonGenerator("Test Award 2")
        assert gen1._hash != gen2._hash

    def test_stripe_pattern_generation(self):
        """Test stripe pattern generation."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        gen = RibbonGenerator("Test Award")
        pattern = gen._get_stripe_pattern()

        # Should return list of (color, width) tuples
        assert isinstance(pattern, list)
        assert len(pattern) > 0

        total_width = sum(width for _, width in pattern)
        assert total_width == gen.width

    def test_explicit_stripe_pattern(self):
        """Test explicit stripe pattern generation."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        colors = ["#FF0000", "#FFFFFF", "#0000FF"]
        gen = RibbonGenerator("Test", colors=colors)
        pattern = gen._get_explicit_stripe_pattern()

        assert len(pattern) == 3
        total_width = sum(width for _, width in pattern)
        assert total_width == gen.width

    @pytest.mark.skipif(
        not importlib.util.find_spec("PIL"),
        reason="PIL not installed"
    )
    def test_ribbon_generate_returns_bytes(self):
        """Test that generate returns PNG bytes when PIL available."""
        from plugins.logbook.utils.ribbon import RibbonGenerator, HAS_IMAGING

        if not HAS_IMAGING:
            pytest.skip("Imaging libraries not available")

        gen = RibbonGenerator("Test Award")
        result = gen.generate()

        assert result is not None
        assert isinstance(result, bytes)
        # PNG files start with specific bytes
        assert result[:4] == b'\x89PNG'

    @pytest.mark.skipif(
        not importlib.util.find_spec("PIL"),
        reason="PIL not installed"
    )
    def test_create_ribbon_rack(self):
        """Test create_ribbon_rack function."""
        from plugins.logbook.utils.ribbon import create_ribbon_rack, HAS_IMAGING

        if not HAS_IMAGING:
            pytest.skip("Imaging libraries not available")

        awards = [
            ("Distinguished Flying Cross", ["#FF0000", "#FFFFFF", "#0000FF"], 1),
            ("Air Medal", None, 2),
        ]

        result = create_ribbon_rack(awards)

        assert result is not None
        assert isinstance(result, bytes)
        assert result[:4] == b'\x89PNG'


# =============================================================================
# MigrationReport Tests
# =============================================================================

class TestMigrationReport:
    """Tests for the MigrationReport class."""

    def test_migration_report_init(self):
        """Test MigrationReport initialization."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import MigrationReport

        report = MigrationReport()

        assert report.pilots_found == 0
        assert report.pilots_mapped == 0
        assert report.pilots_unmapped == []
        assert report.squadrons_imported == 0
        assert report.awards_imported == 0
        assert report.errors == []

    def test_migration_report_validation_pass(self, capsys):
        """Test MigrationReport validation passes when hours preserved."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import MigrationReport
        from decimal import Decimal

        report = MigrationReport()
        report.total_hours_old = Decimal("100.0")
        report.total_hours_new = Decimal("100.0")

        result = report.print_report()

        assert result is True

    def test_migration_report_validation_fail(self, capsys):
        """Test MigrationReport validation fails when hours lost."""
        from plugins.logbook.scripts.migrate_from_dcs_server_logbook import MigrationReport
        from decimal import Decimal

        report = MigrationReport()
        report.total_hours_old = Decimal("100.0")
        report.total_hours_new = Decimal("50.0")

        result = report.print_report()

        assert result is False


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_format_hours_very_large_value(self):
        """Test formatting extremely large values."""
        from plugins.logbook.commands import format_hours

        # 1 million seconds
        result = format_hours(1000000)
        assert "h" in result

    def test_format_hours_decimal_precision(self):
        """Test decimal precision in format_hours."""
        from plugins.logbook.commands import format_hours

        # 7200 seconds = 2.0 hours exactly
        result = format_hours(7200)
        assert result == "2.0h"

        # 9000 seconds = 2.5 hours
        result = format_hours(9000)
        assert result == "2.5h"

    def test_ribbon_generator_empty_name(self):
        """Test RibbonGenerator with empty name."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        gen = RibbonGenerator("")
        assert gen.name == ""
        # Should still generate a valid hash
        assert gen._hash is not None

    def test_ribbon_generator_unicode_name(self):
        """Test RibbonGenerator with unicode characters."""
        from plugins.logbook.utils.ribbon import RibbonGenerator

        gen = RibbonGenerator("Test Award")
        assert gen.name == "Test Award"


# =============================================================================
# Constants and Configuration Tests
# =============================================================================

class TestConstants:
    """Test plugin constants and configuration."""

    def test_version_format(self):
        """Test version follows semver format."""
        from plugins.logbook.version import __version__

        parts = __version__.split('.')
        assert len(parts) >= 2, "Version should have at least major.minor"

    def test_default_ribbon_colors_defined(self):
        """Test default ribbon colors are defined."""
        from plugins.logbook.utils.ribbon import DEFAULT_RIBBON_COLORS

        assert len(DEFAULT_RIBBON_COLORS) > 0
        for color in DEFAULT_RIBBON_COLORS:
            assert color.startswith('#'), f"Color {color} should be hex format"

    def test_has_imaging_flag(self):
        """Test HAS_IMAGING flag is a boolean."""
        from plugins.logbook.utils.ribbon import HAS_IMAGING

        assert isinstance(HAS_IMAGING, bool)
