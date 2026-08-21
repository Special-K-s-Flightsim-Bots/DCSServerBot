from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class UserEntry(BaseModel):
    ucid: str = Field(..., description="DCS account ID")
    discord_id: int = Field(..., description="Discord user ID")
    nick: str = Field(..., description="Player nickname")
    date: datetime = Field(..., description="Last seen timestamp")
    current_server: Optional[str] = Field(None, description="Current server")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        },
        "json_schema_extra": {
            "example": {
                "ucid": "aabbccddeeffgghhiiffkk1234567890",
                "discord_id": 123456789012345678,
                "nick": "Player1",
                "date": "2025-08-07T12:00:00",
                "current_server": "My Fancy Server"
            }
        }
    }


class DailyPlayers(BaseModel):
    date: datetime
    player_count: int

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        },
        "json_schema_extra": {
            "example": {
                "date": "2025-08-07T12:00:00",
                "player_count": 100
            }
        }
    }


class ServerStats(BaseModel):
    totalPlayers: int
    avgPlaytime: int
    totalPlaytime: int
    activePlayers: int
    totalSorties: int
    totalKills: int
    totalDeaths: int
    totalPvPKills: int
    totalPvPDeaths: int
    daily_players: list[DailyPlayers]

    model_config = {
        "json_schema_extra": {
            "example": {
                "totalPlayers": 100,
                "avgPlaytime": 120,
                "totalPlaytime": 3600,
                "activePlayers": 50,
                "totalSorties": 100,
                "totalKills": 100,
                "totalDeaths": 50,
                "totalPvPKills": 30,
                "totalPvPDeaths": 20,
                "daily_players": [
                    {
                        "date": "2025-08-07T12:00:00",
                        "player_count": 100
                    }
                ]
            }
        }
    }


class MissionInfo(BaseModel):
    name: str
    uptime: int
    date_time: str
    theatre: str
    blue_slots: Optional[int] = None
    blue_slots_used: Optional[int] = None
    red_slots: Optional[int] = None
    red_slots_used: Optional[int] = None
    restart_time: Optional[int] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Training Mission",
                "uptime": 3600,
                "date_time": "2025-08-07 12:00:00",
                "theatre": "Caucasus",
                "blue_slots": 20,
                "blue_slots_used": 5,
                "red_slots": 20,
                "red_slots_used": 3,
                "restart_time": 1691424000
            }
        }
    }


class ExtensionInfo(BaseModel):
    name: str
    version: str | None = None
    value: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "SRS",
                "version": "1.9.0.0",
                "value": "127.0.0.1:5002"
            }
        }
    }


class SquadronInfo(BaseModel):
    name: str = Field(..., description="Name of the squadron")
    description: str = Field(..., description="Description of the squadron")
    image_url: str = Field(..., description="URL to the squadron's image")
    locked: bool = Field(..., description="Whether the squadron is locked")
    role: str | None = Field(None, description="Discord role name associated with the squadron")
    members: list[UserEntry] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Red Devils",
                "description": "Elite Fighter Squadron",
                "image_url": "https://example.com/squadron-logo.png",
                "locked": True,
                "role": "Squadron Leader",
                "members": [
                    {
                        "ucid": "aabbccddeeffgghhiiffkk1234567890",
                        "discord_id": 123456789012345678,
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "current_server": "My Fancy Server"
                    }
                ]
            }
        }
    }


class TopKill(BaseModel):
    row_num: int = Field(..., description="Row number")
    nick: str = Field(..., description="Player's nickname")
    date: datetime = Field(..., description="Last seen date of that player in ISO-format")
    kills: int = Field(..., description="Number of kills")
    deaths: int = Field(..., description="Number of deaths")
    kdr: float = Field(..., description="Kill/Death ratio")
    kills_pvp: int = Field(..., description="Number of kills in PvP")
    deaths_pvp: int = Field(..., description="Number of deaths in PvP")
    kdr_pvp: float = Field(..., description="Kill/Death ratio in PvP")
    playtime: int = Field(..., description="Total playtime in seconds")
    credits: int = Field(..., description="Total credits earned")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        },
        "json_schema_extra": {
            "example": {
                "row_num": 1,
                "nick": "Special K",
                "date": "2025-01-01T00:00:00",
                "kills": 10,
                "deaths": 2,
                "kdr": 5.0,
                "kills_pvp": 5,
                "deaths_pvp": 0,
                "kdr_pvp": 5.0,
                "playtime": 7200,
                "credits": 1500
            }
        }
    }


class LeaderBoard(BaseModel):
    items: list[TopKill]
    total_count: int
    offset: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "row_num": 1,
                        "nick": "Special K",
                        "date": "2025-01-01T00:00:00",
                        "kills": 10,
                        "deaths": 2,
                        "kdr": 5.0,
                        "kills_pvp": 5,
                        "deaths_pvp": 0,
                        "kdr_pvp": 5.0,
                        "playtime": 7200,
                        "credits": 1500
                    }
                ],
                "total_count": 1,
                "offset": 0
            }
        }
    }


class Trueskill(BaseModel):
    nick: str = Field(..., description="Player's nickname")
    date: datetime = Field(..., description="Last seen date of that player in ISO-format")
    kills_pvp: int = Field(..., description="Number of PvP kills")
    deaths_pvp: int = Field(..., description="Number of deaths by other players")
    TrueSkill: float = Field(..., description="TrueSkill:tm: Rating of that player")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat(),
            Decimal: lambda v: float(v)
        },
        "json_schema_extra": {
            "example": {
                "nick": "Special K",
                "date": "2025-01-01T00:00:00",
                "kills_pvp": 10,
                "deaths_pvp": 2,
                "TrueSkill": 18.6
            }
        }
    }


class HighscoreEntry(BaseModel):
    nick: str = Field(..., description="Player nickname")
    date: datetime = Field(..., description="Last seen timestamp")
    value: Decimal = Field(..., description="Score value (varies by category)")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat(),
            Decimal: lambda v: float(v)
        },
        "json_schema_extra": {
            "example": {
                "nick": "Player1",
                "date": "2025-08-07T12:00:00",
                "value": 42.0
            }
        }
    }


class PlaytimeEntry(BaseModel):
    nick: str = Field(..., description="Player nickname")
    date: datetime = Field(..., description="Last seen timestamp")
    playtime: int = Field(..., description="Total playtime in seconds")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        },
        "json_schema_extra": {
            "example": {
                "nick": "Player1",
                "date": "2025-08-07T12:00:00",
                "playtime": 3600
            }
        }
    }


class Highscore(BaseModel):
    playtime: list[PlaytimeEntry] = Field(default_factory=list, description="Playtime rankings")
    air_targets: list[HighscoreEntry] = Field(default_factory=list, alias="Air Targets",
                                              description="Air kills rankings")
    ships: list[HighscoreEntry] = Field(default_factory=list, alias="Ships", description="Ship kills rankings")
    air_defence: list[HighscoreEntry] = Field(default_factory=list, alias="Air Defence",
                                              description="SAM kills rankings")
    ground_targets: list[HighscoreEntry] = Field(default_factory=list, alias="Ground Targets",
                                                 description="Ground kills rankings")
    kd_ratio: list[HighscoreEntry] = Field(default_factory=list, alias="KD-Ratio",
                                           description="Kill/Death ratio rankings")
    pvp_kd_ratio: list[HighscoreEntry] = Field(default_factory=list, alias="PvP-KD-Ratio",
                                               description="PvP Kill/Death ratio rankings")
    most_efficient_killers: list[HighscoreEntry] = Field(default_factory=list, alias="Most Efficient Killers",
                                                         description="Kills per hour rankings")
    most_wasteful_pilots: list[HighscoreEntry] = Field(default_factory=list, alias="Most Wasteful Pilots",
                                                       description="Crashes per hour rankings")

    model_config = {
        "populate_by_name": False,
        "validate_by_name": True,
        "json_schema_extra": {
            "example": {
                "playtime": [
                    {
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "playtime": 3600
                    }
                ],
                "Air Targets": [
                    {
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "value": 42.0
                    }
                ],
                "Ships": [
                    {
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "value": 3.0
                    }
                ],
                "Air Defence": [
                    {
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "value": 12.0
                    }
                ],
                "Ground Targets": [
                    {
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "value": 55.0
                    }
                ],
                "KD-Ratio": [
                    {
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "value": 3.5
                    }
                ],
                "PvP-KD-Ratio": [
                    {
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "value": 2.5
                    }
                ],
                "Most Efficient Killers": [
                    {
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "value": 15.2
                    }
                ],
                "Most Wasteful Pilots": [
                    {
                        "nick": "Player1",
                        "date": "2025-08-07T12:00:00",
                        "value": 1.1
                    }
                ]
            }
        }
    }


class WeaponPK(BaseModel):
    weapon: str = Field(..., description="Weapon type")
    shots: int = Field(..., description="Number of shots fired")
    hits: int = Field(..., description="Number of hits")
    pk: Decimal = Field(..., description="Probability of killing")

    model_config = {
        "json_encoders": {
            Decimal: lambda v: float(v)
        },
        "json_schema_extra": {
            "example": {
                "weapon": "AIM-120C",
                "shots": 20,
                "hits": 10,
                "pk": 0.5
            }
        }
    }


class ModuleStats(BaseModel):
    module: str = Field(..., description="Aircraft/module name")
    kills: int | None = Field(None, description="Number of kills with this module")
    deaths: int | None = Field(None, description="Number of deaths with this module")
    kdr: Decimal | None = Field(None, description="Kill/Death ratio with this module")
    playtime: int | None = Field(None, description="Total playtime with this module in seconds")

    model_config = {
        "json_encoders": {
            Decimal: lambda v: float(v)
        },
        "json_schema_extra": {
            "example": {
                "module": "F/A-18C",
                "kills": 30,
                "deaths": 10,
                "kdr": 3.0,
                "playtime": 3600
            }
        }
    }


class PlayerStats(BaseModel):
    playtime: int = Field(..., description="Total playtime in seconds")
    kills: int = Field(..., description="Total kills")
    deaths: int = Field(..., description="Total deaths")
    kills_pvp: int = Field(..., description="Total PvP kills")
    deaths_pvp: int = Field(..., description="Total PvP deaths")
    kills_planes: int = Field(..., description="Total plane kills")
    kills_helicopters: int = Field(..., description="Total helicopter kills")
    kills_ships: int = Field(..., description="Total ship kills")
    kills_sams: int = Field(..., description="Total SAM kills")
    kills_ground: int = Field(..., description="Total ground kills")
    deaths_planes: int = Field(..., description="Total plane deaths")
    deaths_helicopters: int = Field(..., description="Total helicopter deaths")
    deaths_ships: int = Field(..., description="Total ship deaths")
    deaths_sams: int = Field(..., description="Total SAM deaths")
    deaths_ground: int = Field(..., description="Total ground deaths")
    takeoffs: int = Field(..., description="Number of takeoffs")
    landings: int = Field(..., description="Number of landings")
    ejections: int = Field(..., description="Number of ejections")
    crashes: int = Field(..., description="Number of crashes")
    teamkills: int = Field(..., description="Number of team kills")
    kdr: Decimal = Field(..., description="Kill/death ratio")
    kdr_pvp: Decimal = Field(..., description="PvP Kill/death ratio")
    killsByModule: list[ModuleStats] = Field(default_factory=list, description="PvP-Kills breakdown by module")
    kdrByModule: list[ModuleStats] = Field(default_factory=list, description="PvP-KDR breakdown by module")

    model_config = {
        "json_encoders": {
            Decimal: lambda v: float(v)
        },
        "json_schema_extra": {
            "example": {
                "playtime": 3600,
                "kills": 100,
                "deaths": 20,
                "kills_pvp": 50,
                "deaths_pvp": 20,
                "kills_planes": 40,
                "kills_helicopters": 10,
                "kills_ships": 5,
                "kills_sams": 15,
                "kills_ground": 30,
                "deaths_planes": 10,
                "deaths_helicopters": 2,
                "deaths_ships": 1,
                "deaths_sams": 5,
                "deaths_ground": 2,
                "takeoffs": 200,
                "landings": 180,
                "ejections": 5,
                "crashes": 15,
                "teamkills": 2,
                "kdr": 2.5,
                "kdr_pvp": 2.5,
                "killsByModule": [
                    {
                        "module": "F/A-18C",
                        "kills": 30
                    }
                ],
                "kdrByModule": [
                    {
                        "module": "F/A-18C",
                        "kdr": 2.5
                    }
                ]
            }
        }
    }


class WeatherInfo(BaseModel):
    temperature: float | None = Field(None, description="Temperature in Celsius")
    wind_speed: float | None = Field(None, description="Wind speed in kts")
    wind_direction: int | None = Field(None, description="Wind direction in degrees")
    pressure: float | None = Field(None, description="Atmospheric pressure in mmHg")
    clouds_base: int | None = Field(None, description="Cloud base altitude in feet")
    clouds_density: int | None = Field(None, description="Cloud density (0-10)")
    clouds_thickness: int | None = Field(None, description="Cloud thickness in feet")
    precipitation: int | None = Field(None, description="Precipitation type (0=none, 1=rain, 2=thunderstorm, 3=snow)")
    fog_enabled: bool | None = Field(None, description="Fog enabled")
    dust_enabled: bool | None = Field(None, description="Dust storm enabled")
    visibility: int | None = Field(None, description="Visibility in meters")

    model_config = {
        "json_schema_extra": {
            "example": {
                "temperature": 15.5,
                "wind_speed": 5.2,
                "wind_direction": 270,
                "pressure": 760.0,
                "visibility": 9999,
                "clouds_base": 8000,
                "clouds_density": 4,
                "clouds_thickness": 1000,
                "precipitation": 0,
                "fog_enabled": False,
                "dust_enabled": False
            }
        }
    }


class PlayerEntry(BaseModel):
    nick: str = Field(..., description="Player name")
    side: str = Field(..., description="Player side")
    unit_type: str = Field(..., description="Type of aircraft")
    callsign: str = Field(..., description="Callsign of the aircraft")
    radios: list[int] = Field(..., description="List of radios")

    model_config = {
        "json_schema_extra": {
            "example": {
                "nick": "Pilot1",
                "side": "blue",
                "unit_type": "FA-18C_hornet",
                "callsign": "Chevy 1-1",
                "radios": [127500000, 251000000]
            }
        }
    }


class ServerInfo(BaseModel):
    name: str = Field(..., description="Name of the server")
    description: str = Field(..., description="Description of the server")
    status: str = Field(..., description="Server status")
    address: str = Field(..., description="IP address and port")
    password: str = Field(..., description="Server password")
    restart_time: datetime | None = Field(None, description="Restart time")
    max_players: int | None = Field(None, description="Maximum number of players")
    require_pure_clients: bool = Field(..., description="Whether to require pure clients")
    require_pure_models: bool = Field(..., description="Whether to require pure models")
    require_pure_scripts: bool = Field(..., description="Whether to require pure scripts")
    require_pure_textures: bool = Field(..., description="Whether to require pure textures")
    mission: MissionInfo | None = Field(None, description="Mission info")
    extensions: list[ExtensionInfo] = Field(default_factory=list)
    players: list[PlayerEntry] = Field(default_factory=list)
    weather: WeatherInfo | None = Field(None, description="Current weather information")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        },
        "json_schema_extra": {
            "example": {
                "name": "DCS Server",
                "description": "Public Dedicated Server",
                "status": "running",
                "address": "127.0.0.1:10308",
                "password": "secret",
                "restart_time": "2025-08-07T12:00:00",
                "max_players": 32,
                "require_pure_clients": True,
                "require_pure_models": True,
                "require_pure_scripts": True,
                "require_pure_textures": True,
                "mission": {
                    "name": "Training Mission",
                    "uptime": 3600,
                    "date_time": "2025-08-07 12:00:00",
                    "theatre": "Caucasus",
                    "blue_slots": 20,
                    "blue_slots_used": 5,
                    "red_slots": 20,
                    "red_slots_used": 3,
                    "restart_time": 1691424000
                },
                "extensions": [
                    {
                        "name": "SRS",
                        "version": "1.9.0.0",
                        "value": "127.0.0.1:5002"
                    }
                ],
                "players": [
                    {
                        "nick": "Pilot1",
                        "side": "blue",
                        "unit_type": "FA-18C_hornet",
                        "callsign": "Chevy 1-1",
                        "radios": [127500000, 251000000]
                    }
                ],
                "weather": {
                    "temperature": 15.5,
                    "wind_speed": 5.2,
                    "wind_direction": 270,
                    "pressure": 760.0,
                    "visibility": 9999,
                    "clouds_base": 8000,
                    "clouds_density": 4,
                    "clouds_thickness": 1000,
                    "precipitation": 0,
                    "fog_enabled": False,
                    "dust_enabled": False
                }
            }
        }
    }


class CampaignCredits(BaseModel):
    id: int = Field(..., description="Campaign ID")
    name: str = Field(..., description="Campaign name")
    credits: float = Field(..., description="Player's credits in this campaign")
    rank: str | None = Field(..., description="Player's rank")
    badge: str | None = Field(..., description="Player's badge")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": 1,
                "name": "Summer Campaign 2025",
                "credits": 1500.0,
                "rank": "Rookie",
                "badge": "https://example.com/rookie_badge.png"
            }
        }
    }


class TrapEntry(BaseModel):
    id: int = Field(..., description="Trap ID")
    unit_type: str = Field(..., description="Type of aircraft")
    grade: str = Field(..., description="Landing grade")
    comment: str = Field(..., description="Landing comment")
    place: str = Field(..., description="Landing location")
    trapcase: int = Field(..., description="Trap case number")
    wire: int | None = Field(..., description="Arresting wire number")
    night: bool = Field(..., description="Whether landing was at night")
    points: int = Field(..., description="Points awarded for the trap")
    time: datetime = Field(..., description="Time of the trap")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        },
        "json_schema_extra": {
            "example": {
                "id": 1,
                "unit_type": "F/A-18C",
                "grade": "OK",
                "comment": "Good pass",
                "place": "CVN-73",
                "trapcase": 3,
                "wire": 3,
                "night": False,
                "points": 100,
                "time": "2025-08-07T12:00:00"
            }
        }
    }


class GreenieboardEntry(BaseModel):
    nick: str = Field(..., description="Player name")
    traps: list[TrapEntry] = Field(..., description="List of traps for this player")

    model_config = {
        "json_schema_extra": {
            "example": {
                "nick": "Player1",
                "traps": [
                    {
                        "id": 1,
                        "unit_type": "F/A-18C",
                        "grade": "OK",
                        "comment": "Good pass",
                        "place": "CVN-73",
                        "trapcase": 3,
                        "wire": 3,
                        "night": False,
                        "points": 100,
                        "time": "2025-08-07T12:00:00"
                    }
                ]
            }
        }
    }


class GreenieboardResponse(BaseModel):
    players: list[GreenieboardEntry] = Field(..., description="All players and their traps")

    model_config = {
        "json_schema_extra": {
            "example": {
                "players": [
                    {
                        "nick": "Player1",
                        "traps": [
                            {
                                "id": 1,
                                "unit_type": "F/A-18C",
                                "grade": "OK",
                                "comment": "Good pass",
                                "place": "CVN-73",
                                "trapcase": 3,
                                "wire": 3,
                                "night": False,
                                "points": 100,
                                "time": "2025-08-07T12:00:00"
                            }
                        ]
                    }
                ]
            }
        }
    }


class EventEntry(BaseModel):
    mission_id: int = Field(..., description="Mission ID")
    event: str = Field(..., description="Event type")
    init_id: str | None = Field(None, description="Initiator UCID")
    init_side: int | None = Field(None, description="Initiator side")
    init_type: str | None = Field(None, description="Initiator type")
    init_cat: str | None = Field(None, description="Initiator category")
    target_id: str | None = Field(None, description="Target UCID")
    target_side: int | None = Field(None, description="Target side")
    target_type: str | None = Field(None, description="Target type")
    target_cat: str | None = Field(None, description="Target category")
    weapon: str | None = Field(None, description="Weapon used in the event")
    place: str | None = Field(None, description="Event location")
    comment: str | None = Field(None, description="Event comment")
    time: datetime = Field(..., description="Event time")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        },
        "json_schema_extra": {
            "example": {
                "mission_id": 1,
                "event": "S_EVENT_KILL",
                "init_id": "aabbccddeeffgghhiiffkk1234567890",
                "init_side": 2,
                "init_type": "FA-18C_hornet",
                "init_cat": "Airplanes",
                "target_id": "11223344556677889900aabbccddeeff",
                "target_side": 1,
                "target_type": "MiG-29A",
                "target_cat": "Airplanes",
                "weapon": "AIM-120C",
                "place": "Over the Caucasus",
                "comment": "First kill of the day!",
                "time": "2025-08-07T12:00:00"
            }
        }
    }


class SquadronCampaignCredit(BaseModel):
    campaign: str | None = Field(None, description="Campaign name")
    credits: float | None = Field(None, description="Squadron's credits in the campaign")

    model_config = {
        "json_schema_extra": {
            "example": {
                "campaign": "Summer Campaign 2025",
                "credits": 1500.0
            }
        }
    }


class PlayerSquadron(BaseModel):
    name: str = Field(..., description="Squadron name")
    image_url: str = Field(..., description="URL of the squadron's image")

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Red Devils",
                "image_url": "https://example.com/squadron-logo.png"
            }
        }
    }


class PlayerInfo(BaseModel):
    current_server: str | None = Field(None, description="Current server")
    overall: PlayerStats = Field(..., description="Overall statistics")
    last_session: PlayerStats = Field(..., description="Statistics of the last session")
    module_stats: list[ModuleStats] = Field(default_factory=list, description="Statistics by module")
    credits: CampaignCredits | None = Field(None, description="Campaign credits of this player")
    squadrons: list[PlayerSquadron] = Field(default_factory=list, description="Squadrons the player is a member of")

    model_config = {
        "json_schema_extra": {
            "example": {
                "current_server": "DCS Server",
                "overall": {
                    "playtime": 7200,
                    "kills": 150,
                    "deaths": 30,
                    "kills_pvp": 80,
                    "deaths_pvp": 25,
                    "kills_planes": 60,
                    "kills_helicopters": 15,
                    "kills_ships": 5,
                    "kills_sams": 20,
                    "kills_ground": 50,
                    "deaths_planes": 15,
                    "deaths_helicopters": 3,
                    "deaths_ships": 1,
                    "deaths_sams": 8,
                    "deaths_ground": 3,
                    "takeoffs": 300,
                    "landings": 270,
                    "ejections": 8,
                    "crashes": 22,
                    "teamkills": 1,
                    "kdr": 5.0,
                    "kdr_pvp": 3.2,
                    "killsByModule": [{"module": "F/A-18C", "kills": 50}],
                    "kdrByModule": [{"module": "F/A-18C", "kdr": 3.5}]
                },
                "last_session": {
                    "playtime": 3600,
                    "kills": 10,
                    "deaths": 2,
                    "kills_pvp": 5,
                    "deaths_pvp": 1,
                    "kills_planes": 4,
                    "kills_helicopters": 1,
                    "kills_ships": 0,
                    "kills_sams": 2,
                    "kills_ground": 3,
                    "deaths_planes": 1,
                    "deaths_helicopters": 0,
                    "deaths_ships": 0,
                    "deaths_sams": 1,
                    "deaths_ground": 0,
                    "takeoffs": 3,
                    "landings": 3,
                    "ejections": 0,
                    "crashes": 0,
                    "teamkills": 0,
                    "kdr": 5.0,
                    "kdr_pvp": 5.0,
                    "killsByModule": [{"module": "F/A-18C", "kills": 10}],
                    "kdrByModule": [{"module": "F/A-18C", "kdr": 5.0}]
                },
                "module_stats": [
                    {
                        "module": "F/A-18C",
                        "kills": 30,
                        "deaths": 10,
                        "kdr": 3.0,
                        "playtime": 3600
                    }
                ],
                "credits": {
                    "id": 1,
                    "name": "Summer Campaign 2025",
                    "credits": 1500.0,
                    "rank": "Rookie",
                    "badge": "https://example.com/rookie_badge.png"
                },
                "squadrons": [
                    {
                        "name": "Red Devils",
                        "image_url": "https://example.com/squadron-logo.png"
                    }
                ]
            }
        }
    }


class LinkMeResponse(BaseModel):
    token: str | None = Field(None, description="4-digit token for linking DCS and Discord accounts")
    timestamp: str | None = Field(None, description="Expiry timestamp in ISO format")
    rc: int = Field(..., description="Return code bitmask (1=User linked, 2=Link in progress, 4=Force operation)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "token": "1234",
                "timestamp": "2025-08-09T12:00:00+00:00",
                "rc": 2
            }
        }
    }


class TopTheatre(BaseModel):
    theatre: str
    playtime_hours: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "theatre": "Caucasus",
                "playtime_hours": 2500
            }
        }
    }


class TopMission(BaseModel):
    mission_name: str
    playtime_hours: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "mission_name": "Training Map",
                "playtime_hours": 1200
            }
        }
    }


class TopModule(BaseModel):
    module: str
    playtime_hours: int
    unique_players: int
    total_uses: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "module": "F/A-18C",
                "playtime_hours": 800,
                "unique_players": 45,
                "total_uses": 127
            }
        }
    }


class ServerAttendanceStats(BaseModel):
    """Server attendance statistics using monitoring plugin patterns"""
    current_players: int = Field(..., description="Current number of active players")

    # Statistics for different periods (24h, 7d, 30d) following monitoring plugin patterns
    unique_players_24h: int = Field(..., description="Unique players in last 24 hours")
    total_playtime_hours_24h: float = Field(..., description="Total playtime hours in last 24 hours")
    discord_members_24h: int = Field(..., description="Discord members who played in last 24 hours")

    unique_players_7d: int = Field(..., description="Unique players in last 7 days")
    total_playtime_hours_7d: float = Field(..., description="Total playtime hours in last 7 days")
    discord_members_7d: int = Field(..., description="Discord members who played in last 7 days")

    unique_players_30d: int = Field(..., description="Unique players in last 30 days")
    total_playtime_hours_30d: float = Field(..., description="Total playtime hours in last 30 days")
    discord_members_30d: int = Field(..., description="Discord members who played in last 30 days")

    # Daily trend for the last week
    daily_trend: list[dict] = Field(default_factory=list, description="Daily unique player counts for trend analysis")

    # Enhanced statistics from the Discord /serverstats command
    top_theatres: list[TopTheatre] = Field(default_factory=list, description="Top theatres by playtime")
    top_missions: list[TopMission] = Field(default_factory=list, description="Top missions by playtime")
    top_modules: list[TopModule] = Field(default_factory=list, description="Top modules by playtime and usage")

    # Additional server metrics from mv_serverstats
    total_sorties: int | None = Field(None, description="Total sorties flown")
    total_kills: int | None = Field(None, description="Total kills")
    total_deaths: int | None = Field(None, description="Total deaths")
    total_pvp_kills: int | None = Field(None, description="Total PvP kills")
    total_pvp_deaths: int | None = Field(None, description="Total PvP deaths")

    model_config = {
        "json_schema_extra": {
            "example": {
                "current_players": 8,
                "unique_players_24h": 15,
                "total_playtime_hours_24h": 45.5,
                "discord_members_24h": 12,
                "unique_players_7d": 35,
                "total_playtime_hours_7d": 180.2,
                "discord_members_7d": 28,
                "unique_players_30d": 85,
                "total_playtime_hours_30d": 720.8,
                "discord_members_30d": 65,
                "daily_trend": [
                    {"date": "2025-12-24", "unique_players": 15},
                    {"date": "2025-12-25", "unique_players": 18}
                ],
                "top_theatres": [{"theatre": "Caucasus", "playtime_hours": 2500}, {"theatre": "Syria", "playtime_hours": 347}],
                "top_missions": [{"mission_name": "Training Map", "playtime_hours": 1200}, {"mission_name": "Combat Mission", "playtime_hours": 800}],
                "top_modules": [{"module": "F/A-18C", "playtime_hours": 800, "unique_players": 45, "total_uses": 127}],
                "total_sorties": 1245,
                "total_kills": 892,
                "total_deaths": 567,
                "total_pvp_kills": 234,
                "total_pvp_deaths": 189
            }
        }
    }


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "Server not found"
            }
        }
    }


class Position(BaseModel):
    y: float
    x: float
    z: float

    model_config = {
        "json_schema_extra": {
            "example": {
                "x": 76048.95,
                "y": 250.0,
                "z": 111344.92
            }
        }
    }


class FrequencyListItem(BaseModel):
    # each frequency entry is a two‑element list: [frequency, value]
    frequency: int
    value: int

    @classmethod
    def __get_validators__(cls):
        yield cls.validate_tuple

    @classmethod
    def validate_tuple(cls, v):
        if not (isinstance(v, (list, tuple)) and len(v) == 2):
            raise TypeError('frequency entry must be a 2‑item list')
        return v


class Dynamic(BaseModel):
    dynamicSpawnAvailable: bool
    allowHotSpawn: bool

    model_config = {
        "json_schema_extra": {
            "example": {
                "dynamicSpawnAvailable": True,
                "allowHotSpawn": False
            }
        }
    }


class Airbase(BaseModel):
    alt: float
    code: str | None = None
    id: str | None = None
    lat: float
    rwy_heading: int | None = None
    lng: float
    name: str
    position: Position
    frequencyList: list[list[int]] | list[tuple[int, int]] | dict | None = None
    dynamic: Dynamic
    runwayList: list[str] | dict | None = None
    coalition: str | int | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "alt": 250.0,
                "code": "UGSB",
                "id": "Batumi",
                "lat": 41.6166,
                "rwy_heading": 126,
                "lng": 41.6000,
                "name": "Batumi",
                "position": {
                    "x": 76048.95,
                    "y": 250.0,
                    "z": 111344.92
                },
                "frequencyList": [
                    [131000000, 0],
                    [260000000, 0]
                ],
                "dynamic": {
                    "dynamicSpawnAvailable": True,
                    "allowHotSpawn": False
                },
                "runwayList": ["13", "31"],
                "coalition": 2
            }
        }
    }


class AirbasesResponse(BaseModel):
    airbases: list[Airbase] = Field(..., description="Airbases data")

    model_config = {
        "json_schema_extra": {
            "example": {
                "airbases": [
                    {
                        "alt": 250.00025,
                        "code": "ICAO",
                        "id": "Airbase_Name",
                        "lat": 35.732306452624,
                        "rwy_heading": 274,
                        "lng": 37.104127964423,
                        "name": "Airbase Name",
                        "position": {
                            "y": 250.00025,
                            "x": 76048.957031,
                            "z": 111344.925781
                        },
                        "frequencyList": [
                            [
                                38950000,
                                0
                            ],
                            [
                                122200000,
                                0
                            ],
                            [
                                250500000,
                                0
                            ],
                            [
                                4025000,
                                0
                            ]
                        ],
                        "dynamic": {
                            "dynamicSpawnAvailable": True,
                            "allowHotSpawn": False
                        },
                        "runwayList": [
                            "09",
                            "27"
                        ],
                        "coalition": 1
                    }
                ]
            }
        }
    }


class AirbaseInfoResponse(BaseModel):
    airbase: dict = Field(..., description="Airbase data")

    model_config = {
        "json_schema_extra": {
            "example": {
                "airbase": {
                    "alt": 69.475785112923,
                    "channel": "...",
                    "server_name": "Server Name",
                    "auto_capture": True,
                    "lat": 36.371269972814,
                    "unlimited": {
                        "weapon": False,
                        "liquids": True,
                        "aircraft": False
                    },
                    "parking": [
                        {
                            "Term_Index": 9,
                            "vTerminalPos": {
                                "y": 69.475784301758,
                                "x": 147715.125,
                                "z": 38939.109375
                            },
                            "TO_AC": False,
                            "Term_Index_0": -1,
                            "Term_Type": 104,
                            "fDistToRW": 1641.8400878906
                        }
                    ],
                    "coalition": 2,
                    "lng": 36.298090184913,
                    "name": "Airbase Name",
                    "position": {
                        "y": 69.475784301758,
                        "x": 148653.765625,
                        "z": 40403.9453125
                    },
                    "command": "getAirbase",
                    "warehouse": {
                        "liquids": {
                            "0": 324730.28125,
                            "1": 500000,
                            "2": 500000,
                            "3": 500000
                        },
                        "weapon": {
                            "weapons.missiles.AGM_154": 50,
                            "weapons.nurs.HYDRA_70_M151_M433": 100,
                            "weapons.bombs.BEER_BOMB": 50,
                            "weapons.containers.LANTIRN": 1000,
                            "weapons.droptanks.Spitfire_tank_1": 1000
                        },
                        "aircraft": {
                            "OH58D": 1,
                            "CH-47Fbl1": 1,
                            "A-10C_2": 1,
                            "F-14B": 1
                        }
                    },
                    "runways": [
                        {
                            "course": 2.3682391643524,
                            "Name": 22,
                            "position": {
                                "y": 69.475784301758,
                                "x": 147687.484375,
                                "z": 39418.7421875
                            },
                            "length": 2759.2866210938,
                            "width": 60
                        }
                    ],
                    "radio_silent": True,
                    "mgrs": "37 S BA 56617 27553",
                    "magVar": 5.6234159795293
                }
            }
        }
    }


class PressureInfo(BaseModel):
    pressureHPA: float = Field(..., description="Pressure in hPa")
    pressureMM: float = Field(..., description="Pressure in mmHg")
    pressureIN: float = Field(..., description="Pressure in inHg")

    model_config = {
        "json_schema_extra": {
            "example": {
                "pressureHPA": 1013.25,
                "pressureMM": 760.0,
                "pressureIN": 29.92
            }
        }
    }


class WindInfo(BaseModel):
    speed: float = Field(..., description="Wind speed in m/s")
    dir: float = Field(..., description="Wind direction in degrees")

    model_config = {
        "json_schema_extra": {
            "example": {
                "speed": 5.2,
                "dir": 270.0
            }
        }
    }


class AirbaseAtisResponse(BaseModel):
    command: Optional[str] = Field(None, description="Command name")
    temp: float = Field(..., description="Temperature in Celsius")
    qfe: PressureInfo | dict = Field(..., description="QFE pressure")
    qnh: PressureInfo | dict = Field(..., description="QNH pressure")
    turbulence: Optional[float] = Field(None, description="Turbulence in m/s")
    wind: WindInfo | dict = Field(..., description="Wind conditions")
    weather: Optional[dict] = Field(None, description="Raw mission weather data")
    clouds: Optional[dict] = Field(None, description="Cloud cover information")

    model_config = {
        "json_schema_extra": {
            "example": {
                "command": "getWeatherInfo",
                "temp": 15.5,
                "qfe": {
                    "pressureHPA": 1013.25,
                    "pressureMM": 760.0,
                    "pressureIN": 29.92
                },
                "qnh": {
                    "pressureHPA": 1013.25,
                    "pressureMM": 760.0,
                    "pressureIN": 29.92
                },
                "turbulence": "None",
                "wind": {
                    "speed": 5.2,
                    "dir": 270.0
                },
                "clouds": {
                    "base": 8000,
                    "thickness": 1000,
                    "density": 4
                }
            }
        }
    }


class AirbaseWarehouseResponse(BaseModel):
    warehouse: dict = Field(..., description="Warehouse data")
    unlimited: dict = Field(..., description="Unlimited flags")

    model_config = {
        "json_schema_extra": {
            "example": {
                "warehouse": {
                    "liquids": {
                        "0": 500000,
                        "1": 500000,
                        "2": 500000,
                        "3": 500000
                    },
                    "weapon": {
                        "weapons.missiles.AGM_154": 100,
                        "weapons.nurs.HYDRA_70_M151_M433": 100,
                        "weapons.bombs.GBU_38": 100,
                        "weapons.containers.F-15E_AXQ-14_DATALINK": 100,
                        "weapons.droptanks.FuelTank_350L": 100
                    },
                    "aircraft": {
                        "F-16C_50": 1,
                        "A6E": 100,
                        "AH-64D_BLK_II": 5
                    }
                },
                "unlimited": {
                    "weapon": False,
                    "liquids": True,
                    "aircraft": False
                }
            }
        }
    }


class AirbaseSetWarehouseItemResponse(BaseModel):
    item: str = Field(..., description="Warehouse item name")
    server_name: str = Field(..., description="Server name")
    value: int = Field(..., description="Quantity value")

    model_config = {
        "json_schema_extra": {
            "example": {
                "item": "weapons.bombs.GBU_38",
                "value": 50,
                "server_name": "Server Name"
            }
        }
    }


class AirbaseCaptureResponse(BaseModel):
    server_name: str = Field(..., description="Server name")
    airbase_name: str = Field(..., description="Airbase name")
    coalition: int = Field(..., description="Coalition capturing the airbase")

    model_config = {
        "json_schema_extra": {
            "example": {
                "server_name": "Server Name",
                "airbase_name": "Airbase Name",
                "coalition": 0
            }
        }
    }


class MissionRestartResponse(BaseModel):
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "success",
                "message": "Mission on server 'DCS Server' restarted."
            }
        }
    }


class MissionLoadResponse(BaseModel):
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "success",
                "message": "Mission 'Training Mission.miz' loaded on server 'DCS Server'."
            }
        }
    }


class MissionUploadResponse(BaseModel):
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "success",
                "message": "Mission 'Training Mission.miz' uploaded to server 'DCS Server'."
            }
        }
    }


class MissionPauseResponse(BaseModel):
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "success",
                "message": "Mission on server 'DCS Server' paused."
            }
        }
    }


class MissionUnpauseResponse(BaseModel):
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "success",
                "message": "Mission on server 'DCS Server' resumed."
            }
        }
    }


class MissionEntry(BaseModel):
    name: str = Field(..., description="Mission name without extension")
    path: str = Field(..., description="Relative path to mission file")
    installed: bool = Field(..., description="Whether mission is in the active mission list")

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Training Mission",
                "path": "Training Mission.miz",
                "installed": True
            }
        }
    }


class MissionsResponse(BaseModel):
    missions: list[MissionEntry] = Field(..., description="List of available missions")
    count: int = Field(..., description="Total count of missions")

    model_config = {
        "json_schema_extra": {
            "example": {
                "missions": [
                    {
                        "name": "Training Mission",
                        "path": "Training Mission.miz",
                        "installed": True
                    },
                    {
                        "name": "Combat Scenario",
                        "path": "scenarios/Combat Scenario.miz",
                        "installed": False
                    }
                ],
                "count": 2
            }
        }
    }


class ServerStartResponse(BaseModel):
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "success",
                "message": "Server 'DCS Server' started."
            }
        }
    }


class ServerStopResponse(BaseModel):
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "success",
                "message": "Server 'DCS Server' stopped."
            }
        }
    }


class ServerRestartResponse(BaseModel):
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Status message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "success",
                "message": "Server 'DCS Server' restarted."
            }
        }
    }


class ConvertCoordinates(BaseModel):
    latlon: str = Field(..., description="Latitude and Longitude in decimal degrees")
    mgrs: str = Field(..., description="Cooridnate provided, converted to MGRS")
    dms: str = Field(..., description="Cooridnate provided, converted to Decimal, Minutes, Seconds")
    ddm: str = Field(..., description="Cooridnate provided, converted to Degrees and Decimal Minutes")
    meters: dict = Field(..., description="Cooridnate provided, converted to DCS Meters")

    model_config = {
        "json_schema_extra": {
            "example": {
                "latlon": "35.40556, 35.94889",
                "mgrs": "36S YE 67795 22013",
                "dms": "N 35°24'20.00\" E 035°56'56.00\"",
                "ddm": "N35°24.33333 E35°56.93333",
                "meters": {
                    "x": 42430,
                    "y": 5719
                }
            }
        }
    }


class GroupWaypointsResponse(BaseModel):
    group_name: str = Field(..., description="Name of the group")
    group_type: str = Field(..., description="Type of the group")
    waypoints: dict = Field(..., description="Keyed waypoint dictionary (wp1, wp2, ...) with lat/lon per waypoint")

    model_config = {
        "json_schema_extra": {
            "example": {
                "group_name": "Tanker-1",
                "group_type": "plane",
                "waypoints": {
                    "wp1": {"lat": 36.00001, "lon": 36.00001},
                    "wp2": {"lat": 36.50000, "lon": 36.50000}
                }
            }
        }
    }


class MissionBullseye(BaseModel):
    coalition: str = Field(..., description="Coalition name ('blue' or 'red')")
    lat: float = Field(..., description="Bullseye latitude in decimal degrees")
    lng: float = Field(..., description="Bullseye longitude in decimal degrees")

    model_config = {
        "json_schema_extra": {
            "example": {
                "coalition": "blue",
                "lat": 36.12345,
                "lng": 36.54321
            }
        }
    }


class MissionBullseyesResponse(BaseModel):
    bullseyes: list[MissionBullseye] = Field(..., description="List of coalition bullseye coordinates")

    model_config = {
        "json_schema_extra": {
            "example": {
                "bullseyes": [
                    {"coalition": "blue", "lat": 36.12345, "lng": 36.54321},
                    {"coalition": "red", "lat": 35.98765, "lng": 35.45678}
                ]
            }
        }
    }


class MissionDrawingPoint(BaseModel):
    lat: float = Field(..., description="Latitude in decimal degrees")
    lng: float = Field(..., description="Longitude in decimal degrees")

    model_config = {
        "json_schema_extra": {
            "example": {
                "lat": 36.12345,
                "lng": 36.54321
            }
        }
    }


class MissionDrawing(BaseModel):
    name: str = Field(..., description="Drawing name")
    primitiveType: str = Field(..., description="Drawing primitive type")
    text: str | None = Field(None, description="Optional drawing text")
    layerName: str | None = Field(None, description="Drawing layer name")
    visible: bool | None = Field(None, description="Whether drawing is visible")
    mapX: float | None = Field(None, description="Drawing origin X in mission meters")
    mapY: float | None = Field(None, description="Drawing origin Y in mission meters")
    colorString: str | None = Field(None, description="Stroke color as ARGB hex string")
    fillColorString: str | None = Field(None, description="Fill color as ARGB hex string")
    style: str | None = Field(None, description="Line or fill style")
    thickness: float | int | None = Field(None, description="Line thickness")
    location: MissionDrawingPoint | None = Field(None, description="Drawing anchor location")
    points: list[MissionDrawingPoint] | None = Field(None, description="Optional list of drawing points")
    # Line-specific
    lineMode: str | None = Field(None, description="Line mode (segment, segments, free)")
    closed: bool | None = Field(None, description="Whether line is closed")
    # Polygon-specific
    polygonMode: str | None = Field(None, description="Polygon mode (free, circle, oval, rect, arrow)")
    radius: float | None = Field(None, description="Radius for circle/disc polygons")
    r1: float | None = Field(None, description="First oval radius")
    r2: float | None = Field(None, description="Second oval radius")
    width: float | None = Field(None, description="Width for rectangle polygons")
    height: float | None = Field(None, description="Height for rectangle polygons")
    length: float | None = Field(None, description="Length for arrow polygons")
    angle: float | None = Field(None, description="Drawing rotation angle")
    # TextBox-specific
    font: str | None = Field(None, description="TextBox font file")
    fontSize: float | int | None = Field(None, description="TextBox font size")
    borderThickness: float | int | None = Field(None, description="TextBox border thickness")
    # Icon-specific
    file: str | None = Field(None, description="Icon file name")
    scale: float | None = Field(None, description="Icon scale factor")

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "AO Boundary",
                "primitiveType": "Line",
                "text": "Restricted Area",
                "layerName": "Layer 1",
                "visible": True,
                "mapX": 147687.0,
                "mapY": 39418.0,
                "colorString": "0xFF0000FF",
                "fillColorString": "0x330000FF",
                "style": "solid",
                "thickness": 2,
                "location": {
                    "lat": 36.12345,
                    "lng": 36.54321
                },
                "points": [
                    {"lat": 36.10000, "lng": 36.50000},
                    {"lat": 36.20000, "lng": 36.60000}
                ]
            }
        }
    }

    @field_validator(
        "text",
        "layerName",
        "visible",
        "mapX",
        "mapY",
        "colorString",
        "fillColorString",
        "style",
        "thickness",
        "location",
        "points",
        "lineMode",
        "closed",
        "polygonMode",
        "radius",
        "r1",
        "r2",
        "width",
        "height",
        "length",
        "angle",
        "font",
        "fontSize",
        "borderThickness",
        "file",
        "scale",
        mode="before"
    )
    @classmethod
    def convert_lua_null_placeholder(cls, value):
        # Lua side uses an empty table as a null placeholder so keys survive net.lua2json.
        if isinstance(value, dict) and len(value) == 0:
            return None
        return value


class MissionDrawingsResponse(BaseModel):
    drawings: dict[str, list[dict[str, MissionDrawing]]] = Field(
        ...,
        description="Drawings keyed by layer name; each drawing contains primitive-specific fields"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "drawings": {
                    "Layer 1": [
                        {
                            "name": "AO Boundary",
                            "primitiveType": "Line",
                            "text": None,
                            "location": {"lat": 36.12345, "lng": 36.54321},
                            "points": [
                                {"lat": 36.10000, "lng": 36.50000},
                                {"lat": 36.20000, "lng": 36.60000}
                            ]
                        }
                    ]
                }
            }
        }
    }


class MissionUnitLocation(BaseModel):
    lat: float = Field(..., description="Current latitude in decimal degrees")
    lon: float = Field(..., description="Current longitude in decimal degrees")
    alt: float = Field(..., description="Current altitude in meters")

    model_config = {
        "json_schema_extra": {
            "example": {
                "lat": 13.58402,
                "lon": 144.93082,
                "alt": 58.6
            }
        }
    }


class MissionUnitLoadoutItem(BaseModel):
    displayName: str = Field(..., description="Display name of the weapon or store")
    count: int = Field(..., description="Remaining count")

    model_config = {
        "json_schema_extra": {
            "example": {
                "displayName": "AIM-120C AMRAAM",
                "count": 2
            }
        }
    }


class MissionUnitNavAid(BaseModel):
    active: bool = Field(..., description="Whether this navaid is active")
    channel: int | None = Field(None, description="Configured channel if available")
    modeChannel: str | int | None = Field(None, description="TACAN mode channel, if available")

    model_config = {
        "json_schema_extra": {
            "example": {
                "active": True,
                "channel": 67,
                "modeChannel": "X"
            }
        }
    }


class MissionUnitWaypoint(BaseModel):
    lat: float = Field(..., description="Waypoint latitude in decimal degrees")
    lng: float = Field(..., description="Waypoint longitude in decimal degrees")
    alt: float | None = Field(None, description="Waypoint altitude in meters")
    speed: float | None = Field(None, description="Waypoint speed in m/s")

    model_config = {
        "json_schema_extra": {
            "example": {
                "lat": 13.60000,
                "lng": 145.00000,
                "alt": 3000.0,
                "speed": 180.0
            }
        }
    }


class MissionUnitResponse(BaseModel):
    type: str = Field(..., description="DCS unit type name")
    group_name: str = Field(..., description="DCS group name")
    unit_name: str = Field(..., description="DCS unit name")
    current_location: MissionUnitLocation = Field(..., description="Current unit location")
    speed: float = Field(..., description="Current unit speed in m/s")
    fuel_percentage: float | None = Field(None, description="Current fuel fraction, where 1.0 is 100%")
    life: float | int | None = Field(None, description="Current unit life value")
    in_air: bool | None = Field(None, description="Whether the unit is currently in the air")
    player_name: str | None = Field(None, description="Player name occupying this unit, if any")
    loadout: dict[str, MissionUnitLoadoutItem] = Field(..., description="Loadout keyed by weapon type name")
    tacan: MissionUnitNavAid = Field(..., description="TACAN status and channel data")
    icls: MissionUnitNavAid = Field(..., description="ICLS status and channel data")
    waypoints: list[MissionUnitWaypoint] | None = Field(None, description="Mission waypoints, if available")

    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "FA-18C_hornet",
                "group_name": "Andersen AFB Group",
                "unit_name": "Andersen AFB_F/A-18C Lot 20_0-1",
                "current_location": {
                    "lat": 13.58402,
                    "lon": 144.93082,
                    "alt": 58.6
                },
                "speed": 0.0,
                "fuel_percentage": 0.85,
                "life": 100.0,
                "in_air": False,
                "player_name": "PilotNick",
                "loadout": {
                    "AIM-120C": {
                        "displayName": "AIM-120C AMRAAM",
                        "count": 2
                    }
                },
                "tacan": {
                    "active": True,
                    "channel": 67,
                    "modeChannel": "X"
                },
                "icls": {
                    "active": False,
                    "channel": None,
                    "modeChannel": None
                },
                "waypoints": [
                    {
                        "lat": 13.60000,
                        "lng": 145.00000,
                        "alt": 3000.0,
                        "speed": 180.0
                    }
                ]
            }
        }
    }
