# REST API Plugin Changelog

This document tracks all notable changes made to the REST API plugin.

## [Unreleased] - 2025-12-26

### 🆕 Added

#### Weather Integration in `/servers` Endpoint
- **Comprehensive weather data**: Temperature, wind speed/direction, pressure, visibility, cloud coverage
- **Real-time DCS integration**: Weather extracted from active mission weather system  
- **Configurable**: Control via `include_weather` setting (default: true)

#### New `/server_attendance` Endpoint
- **Multi-period analytics**: 24h, 7d, 30d player statistics with Discord member engagement
- **Enhanced server insights**: Top theatres, missions, and modules by playtime and usage
- **Combat statistics**: Total sorties, kills, deaths, PvP metrics from mv_serverstats
- **Daily trends**: 7-day player activity trends for graphing
- **Inspired by Discord `/serverstats`**: Comprehensive data matching monitoring plugin functionality

#### Configuration Options
- `include_weather: true/false` - Toggle weather in server responses
- `server_attendance.enabled: true/false` - Enable attendance analytics endpoint

### 🔄 Changed  

#### Centralized Server Name Resolution
- **Unified server handling**: All endpoints now use centralized `get_resolved_server()` method
- **Alias support**: Endpoints support both instance aliases (nodes.yaml) and full DCS server names (servers.yaml)  
- **Affected endpoints**: `/serverstats`, `/leaderboard`, `/topkills`, `/topkdr`, `/trueskill`, `/highscore`, `/weaponpk`, `/stats`, `/modulestats`, `/traps`, `/server_attendance`

#### Code Architecture Improvements
- **Simplified SQL construction**: Removed overly complex `get_server_where_clause_and_params()` helper
- **Readable patterns**: Straightforward conditional SQL with direct parameter passing
- **Better maintainability**: Clear, transparent code structure while preserving functionality

### 🐛 Fixed

#### Endpoint Corrections
- **`/modulestats` endpoint**: Fixed incorrect method mapping (`self.stats` → `self.modulestats`) and response model (`ModuleStats` → `list[ModuleStats]`)
- **SQL syntax errors**: Resolved parameter binding issues in database queries
- **Weather data extraction**: Corrected data structure parsing from DCS mission weather

#### Data Model Validation  
- **Response validation**: Fixed Pydantic model validation errors
- **Type consistency**: Proper typing for all new data structures

### 📊 Enhanced Data Models

#### New Pydantic Models
```python
# Weather information
class WeatherInfo(BaseModel):
    temperature: float | None
    wind_speed: float | None  
    wind_direction: int | None
    pressure: float | None
    visibility: float | None
    cloud_coverage: str | None

# Top statistics for server attendance
class TopTheatre(BaseModel):
    theatre: str
    playtime_hours: int

class TopMission(BaseModel):
    mission_name: str
    playtime_hours: int
    
class TopModule(BaseModel):
    module: str
    playtime_hours: int
    unique_players: int
    total_uses: int

# Enhanced server attendance with comprehensive analytics
class ServerAttendanceStats(BaseModel):
    current_players: int
    unique_players_24h: int
    total_playtime_hours_24h: float
    discord_members_24h: int
    unique_players_7d: int  
    total_playtime_hours_7d: float
    discord_members_7d: int
    unique_players_30d: int
    total_playtime_hours_30d: float
    discord_members_30d: int
    daily_trend: list[dict]
    top_theatres: list[TopTheatre]
    top_missions: list[TopMission]  
    top_modules: list[TopModule]
    total_sorties: int | None
    total_kills: int | None
    total_deaths: int | None
    total_pvp_kills: int | None
    total_pvp_deaths: int | None
```

### 🚀 Performance & Technical Details

#### Database Integration
- **Monitoring plugin patterns**: Leverages existing monitoring infrastructure SQL queries
- **Materialized views**: Efficient use of `mv_serverstats` for performance
- **Optimized queries**: Following established DCS server integration patterns

#### Backward Compatibility
- **Zero breaking changes**: All existing API consumers continue working unchanged
- **Opt-in features**: New functionality enabled by default but configurable
- **Migration-free**: No configuration changes required for existing setups

### 📈 Impact

#### For API Consumers
- **Richer server data**: Weather and comprehensive attendance analytics
- **Better server identification**: Flexible server name resolution (aliases + full names)
- **Enhanced dashboards**: More detailed metrics for monitoring and visualization

#### For Developers  
- **Cleaner codebase**: Simplified, maintainable SQL construction patterns
- **Consistent patterns**: Unified server resolution across all endpoints
- **Better documentation**: Complete API models with examples and validation

---

**Summary**: This release significantly enhances the REST API plugin with comprehensive server analytics, weather integration, and improved code architecture while maintaining full backward compatibility.