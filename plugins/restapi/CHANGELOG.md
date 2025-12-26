# REST API Plugin Changelog

This document tracks all notable changes made to the REST API plugin.

## [Unreleased] - 2025-12-26

### Changed

#### Code Simplification
- **Removed Complex Helper Method**: Eliminated `get_server_where_clause_and_params()` method that was overengineered
- **Simplified SQL Construction**: Returned to straightforward conditional SQL patterns using `get_resolved_server()` directly
- **Improved Readability**: SQL queries are now more transparent and easier to understand
- **Maintained Functionality**: All server name resolution features preserved with cleaner implementation

### Added

#### New Features
- **Weather Information in /servers endpoint**: Added comprehensive weather data to server information responses
  - Temperature, wind speed/direction, pressure, visibility, and cloud coverage
  - Configurable via `include_weather` setting (default: true)
  - Weather data extracted from DCS mission weather system

- **Server Attendance Statistics endpoint**: New `/server_attendance` endpoint providing detailed server usage analytics
  - Daily, weekly, and monthly player statistics
  - Peak player counts and average session durations
  - Configurable via `server_attendance.enabled` setting (default: true)
  - Supports both server-specific and global statistics

#### Configuration Schema Updates
- Added `include_weather` boolean option for servers endpoint configuration
- Added `server_attendance.enabled` boolean option for attendance statistics
- Updated configuration validation schema to support new options

### Changed

#### Global Server Name Resolution
- **Centralized server name handling**: Refactored all endpoints that accept `server_name` parameters to use unified resolution system
- **Server alias support**: All endpoints now support both instance aliases (from nodes.yaml) and full DCS server names (from servers.yaml)
- **Consistent behavior**: Standardized server name resolution across all endpoints for improved reliability

#### Affected Endpoints
The following endpoints have been enhanced with centralized server name resolution:
- `/serverstats` - Server statistics endpoint
- `/leaderboard` - Player leaderboard endpoint
- `/topkills` - Top kills leaderboard
- `/topkdr` - Top K/D ratio leaderboard  
- `/trueskill` - TrueSkill rankings
- `/highscore` - High score statistics
- `/weaponpk` - Weapon accuracy statistics
- `/stats` - Individual player statistics
- `/modulestats` - Player module-specific statistics
- `/traps` - Carrier trap statistics
- `/server_attendance` - Server attendance analytics

#### Code Architecture Improvements
- **Helper Methods**: Added centralized utility functions for consistent server resolution:
  - `resolve_server_name()` - Resolves server aliases to actual DCS names
  - `get_resolved_server()` - Returns both resolved name and server object
- **Simplified SQL Construction**: Streamlined SQL query building by removing complex helper methods and using straightforward conditional logic
- **Maintainable Code**: Returned to clear, readable SQL patterns while maintaining centralized server name resolution
- **SQL Query Standardization**: Unified parameter passing and consistent conditional SQL construction patterns
- **Error Handling**: Improved handling of invalid server names and missing servers

### Fixed

#### Bug Fixes
- **SQL Query Issues**: Fixed mixed f-string and parameter usage in database queries
- **Server Stats Endpoint**: Corrected column selection in serverstats query (changed from "currentPlayers" to "totalPlayers")
- **Weather Data Structure**: Fixed weather data extraction to match actual DCS mission weather format
- **Parameter Consistency**: Resolved inconsistent parameter naming between endpoints

#### Configuration Validation
- **Schema Validation**: Fixed configuration schema validation errors for new weather and attendance options
- **Default Values**: Ensured proper default value handling for new configuration options

### Technical Details

#### New Data Models
- **WeatherInfo**: Pydantic model for weather data validation
  ```python
  class WeatherInfo(BaseModel):
      temperature: float | None
      wind_speed: float | None
      wind_direction: int | None
      pressure: float | None
      visibility: float | None
      cloud_coverage: str | None
  ```

- **ServerAttendanceStats**: Pydantic model for attendance statistics
  ```python
  class ServerAttendanceStats(BaseModel):
      period: str
      unique_players: int
      total_sessions: int
      peak_players: int
      average_session_duration: float
      statistics: list[dict]
  ```

#### Database Integration
- **Monitoring Plugin Patterns**: Leveraged existing monitoring infrastructure for attendance statistics
- **Materialized Views**: Utilized existing `mv_serverstats` and `mv_statistics` views
- **Performance Optimization**: Efficient SQL queries with proper indexing considerations

#### API Documentation
- **README Updates**: Comprehensive documentation for all new features and configuration options
- **Configuration Examples**: Updated sample configurations with new options
- **Endpoint Documentation**: Detailed API endpoint descriptions and response examples

### Dependencies
- No new external dependencies required
- Leverages existing FastAPI and Pydantic infrastructure
- Uses established DCS server integration patterns

### Breaking Changes
- None. All changes are backward compatible with existing configurations and API consumers.

### Migration Guide
No migration steps required. New features are opt-in via configuration:

1. **Weather in /servers**: Enabled by default, can be disabled via `include_weather: false`
2. **Server attendance**: Enabled by default, can be disabled via `server_attendance.enabled: false`
3. **Server name resolution**: Automatic upgrade, no configuration changes needed

### Performance Notes
- Weather data extraction has minimal performance impact
- Server attendance queries are optimized for large datasets
- Centralized server resolution reduces code duplication and improves maintainability

---

**Note**: This changelog covers significant enhancements to the REST API plugin, focusing on improved functionality, better code organization, and enhanced user experience while maintaining full backward compatibility.