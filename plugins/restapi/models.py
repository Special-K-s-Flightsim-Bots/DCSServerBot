from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from typing import Optional


class UserEntry(BaseModel):
    nick: str = Field(..., description="Player nickname")
    date: datetime = Field(..., description="Last seen timestamp")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        },
        "json_schema_extra": {
            "example": {
                "nick": "Player1",
                "date": "2025-08-07T12:00:00"
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
            }
        }
    }

class ExtensionInfo(BaseModel):
    name: str
    version: Optional[str] = None
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

class ServerInfo(BaseModel):
    name: str
    status: str
    address: str
    password: str = ""
    mission: Optional[MissionInfo] = None
    extensions: list[ExtensionInfo] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "DCS Server",
                "status": "running",
                "address": "127.0.0.1:10308",
                "password": "secret",
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
                ]
            }
        }
    }


class SquadronInfo(BaseModel):
    name: str = Field(..., description="Name of the squadron")
    description: str = Field(..., description="Description of the squadron")
    image_url: str = Field(..., description="URL to the squadron's image")
    locked: bool = Field(..., description="Whether the squadron is locked")
    role: Optional[str] = Field(None, description="Discord role name associated with the squadron")
    members: list[UserEntry] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Red Devils",
                "description": "Elite Fighter Squadron",
                "image_url": "https://example.com/squadron-logo.png",
                "locked": True,
                "role": "Squadron Leader"
            }
        }
    }


class TopKill(BaseModel):
    nick: str = Field(..., description="Player's nickname")
    date: datetime = Field(..., description="Last seen date of that player in ISO-format")
    kills: int = Field(..., description="Number of kills")
    deaths: int = Field(..., description="Number of deaths")
    kdr: float = Field(..., description="Kill/Death ratio")
    kills_pvp: int = Field(..., description="Number of kills in PvP")
    deaths_pvp: int = Field(..., description="Number of deaths in PvP")
    kdr_pvp: float = Field(..., description="Kill/Death ratio in PvP")

    model_config = {
        "json_schema_extra": {
            "example": {
                "nick": "Special K",
                "date": "2025-01-01T00:00:00",
                "kills": 10,
                "deaths": 2,
                "kdr": 5.0,
                "kills_pvp": 5,
                "deaths_pvp": 0,
                "kdr_pvp": 5.0
            }
        }
    }


class TopKDR(TopKill):
    ...


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
        }
    }


class PlaytimeEntry(BaseModel):
    nick: str = Field(..., description="Player nickname")
    date: datetime = Field(..., description="Last seen timestamp")
    playtime: int = Field(..., description="Total playtime in seconds")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
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
                "playtime": [{
                    "nick": "Player1",
                    "date": "2025-08-07T12:00:00",
                    "playtime": 3600
                }],
                "Air Targets": [{
                    "nick": "Player1",
                    "date": "2025-08-07T12:00:00",
                    "value": 42
                }],
                # ... other categories follow the same pattern
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
    kills: Optional[int] = Field(None, description="Number of PvP-kills with this module")
    kdr: Optional[Decimal] = Field(None, description="PvP-Kill/Death ratio with this module")

    model_config = {
        "json_encoders": {
            Decimal: lambda v: float(v)
        },
        "json_schema_extra": {
            "example": {
                "module": "F/A-18C",
                "kills": 30,
                "kdr": 2.5
            }
        }
    }


class PlayerStats(BaseModel):
    playtime: int = Field(..., description="Total playtime in seconds")
    kills: int = Field(..., description="Total kills")
    deaths: int = Field(..., description="Total deaths")
    kills_pvp: int = Field(..., description="Total PvP kills")
    deaths_pvp: int = Field(..., description="Total PvP deaths")
    takeoffs: int = Field(..., description="Number of takeoffs")
    landings: int = Field(..., description="Number of landings")
    ejections: int = Field(..., description="Number of ejections")
    crashes: int = Field(..., description="Number of crashes")
    teamkills: int = Field(..., description="Number of team kills")
    kdr: Decimal = Field(..., description="Kill/death ratio")
    kdr_pvp: Decimal = Field(..., description="PvP Kill/death ratio")
    lastSessionKills: int = Field(..., description="Kills in last session")
    lastSessionDeaths: int = Field(..., description="Deaths in last session")
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
                "takeoffs": 200,
                "landings": 180,
                "ejections": 5,
                "crashes": 15,
                "teamkills": 2,
                "kdr": 2.5,
                "kdr_pvp": 2.5,
                "lastSessionKills": 10,
                "lastSessionDeaths": 2,
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


class CampaignCredits(BaseModel):
    id: int = Field(..., description="Campaign ID")
    name: str = Field(..., description="Campaign name")
    credits: float = Field(..., description="Player's credits in this campaign")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": 1,
                "name": "Summer Campaign 2025",
                "credits": 1500.0
            }
        }
    }


class TrapEntry(BaseModel):
    unit_type: str = Field(..., description="Type of aircraft")
    grade: str = Field(..., description="Landing grade")
    comment: str = Field(..., description="Landing comment")
    place: str = Field(..., description="Landing location")
    trapcase: int = Field(..., description="Trap case number")
    wire: int = Field(..., description="Arresting wire number")
    night: bool = Field(..., description="Whether landing was at night")
    points: int = Field(..., description="Points awarded for the trap")
    time: datetime = Field(..., description="Time of the trap")

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        },
        "json_schema_extra": {
            "example": {
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


class SquadronCampaignCredit(BaseModel):
    campaign: Optional[str] = Field(None, description="Campaign name")
    credits: Optional[float] = Field(None, description="Squadron's credits in the campaign")

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


class PlayerInfo(PlayerStats):
    credits: Optional[CampaignCredits] = Field(None, description="Campaign credits of this player")
    squadrons: list[PlayerSquadron] = Field(default_factory=list, description="Squadrons the player is a member of")


class LinkMeResponse(BaseModel):
    token: Optional[str] = Field(None, description="4-digit token for linking DCS and Discord accounts")
    timestamp: Optional[str] = Field(None, description="Expiry timestamp in ISO format")
    rc: int = Field(..., description="Return code bitmask (1=User linked, 2=Link in progress, 4=Force operation)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "token": "1234",
                "timestamp": "2025-08-09T12:00:00+00:00",
                "rc": 2  # BIT_LINK_IN_PROGRESS
            }
        }
    }


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error message")
