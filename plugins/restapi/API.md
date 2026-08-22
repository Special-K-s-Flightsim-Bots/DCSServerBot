# RestAPI Endpoints Documentation

This documentation is automatically generated from the RestAPI plugin definitions. It provides a complete reference for all available endpoints, parameters, and responses.

## Table of Contents

- [Airbase](#airbase)
- [Info](#info)
- [Statistics](#statistics)
- [Credits](#credits)
- [Utilities](#utilities)
- [Instance Control](#instance-control)
- [Mission Control](#mission-control)
- [Mission](#mission)
- [Schema Models Reference](#schema-models-reference)

## Summary Overview

| API | GET / POST | Parameters | Description |
|-----|------------|------------|-------------|
| /airbase | GET | server_name: string, airbase_name: string | Get information for a given airbase on a given server. |
| /airbase/atis | GET | server_name: string, airbase_name: string | Get ATIS information for an airbase on a given server. |
| /airbase/capture | POST | server_name: string, airbase_name: string, coalition: int | Capture the airbase. |
| /airbase/warehouse | GET | server_name: string, airbase_name: string | Get warehouse information for an airbase on a given server. |
| /airbase/warehouse/item | POST | server_name: string, airbase_name: string, item: string, value: int | Set warehouse item quantity for an airbase on a given server. |
| /airbases | GET | server_name: string | Get a listing of all airbases on a given server. |
| /convertCoordinates | GET | server_name: string, coordinates: string | Convert provided coordinate string into other formats. |
| /credits | POST | nick: string, [date: string | None], [campaign: string | None] | Get campaign credits for players |
| /current_server | GET | nick: string, [date: string | None] | Server name a player is flying on |
| /events | GET | ucid: string, start_time: datetime, end_time: datetime, [event: string | None], [init_type: string | None], [offset: int | None], [limit: int | None] | Get mission events for players |
| /getuser | POST | [nick: string], [discord_id: string] | Get users by name |
| /greenieboard | POST | [date: string | None], [server_name: string | None] | Get a greenieboard |
| /highscore | GET | [server_name: string], [period: string], [limit: int] | Get highscore statistics for players |
| /instance/mission/load | POST | server_name: string, mission_name: string | Load a mission on a given server. |
| /instance/mission/pause | POST | server_name: string | Pause the mission on a given server. |
| /instance/mission/restart | POST | server_name: string | Restart the mission on a given server. |
| /instance/mission/unpause | POST | server_name: string | Unpause the mission on a given server. |
| /instance/missions | GET | server_name: string | Return all missions for a given server. |
| /instance/restart | POST | server_name: string | Restart a server. |
| /instance/start | POST | server_name: string | Start a server. |
| /instance/stop | POST | server_name: string | Stop a server. |
| /leaderboard | GET | what: string, [order: string], [query: string | None], [limit: int | None], [offset: int | None], [server_name: string | None] | Get leaderbord information |
| /linkme | POST | discord_id: string, [force: bool] | Link your Discord account to your DCS account |
| /mission/bullseyes | GET | server_name: string | Get the bullseye coordinates for blue and red coalitions in the current mission. |
| /mission/drawings | GET | server_name: string | Get mission drawing objects grouped by drawing layer. |
| /mission/group/waypoints | GET | server_name: string, group_name: string, group_type: string | Get the lat/lon waypoints for a named group in the current mission. |
| /mission/unit | GET | server_name: string, unit_name: string | Get mission unit data including current position, loadout, navaids, and waypoints. |
| /mission/upload | POST | server_name: string, file: string, filename: string, load_after: bool | Upload a .miz mission file to the server. |
| /modulestats | POST | nick: string, [date: string | None], [server_name: string | None] | Get module statistics |
| /player_info | POST | nick: string, [date: string | None], [server_name: string | None] | Get player information |
| /player_squadrons | POST | nick: string, [date: string | None] | List of player squadrons |
| /server_attendance | GET | [server_name: string] | Get detailed server attendance statistics |
| /servers | GET | [server_name: string | None] | List all servers, the active mission (if any) and the active extensions |
| /serverstats | GET | [server_name: string] | List the statistics of a whole group |
| /squadron_credits | POST | name: string, [campaign: string] | Squadron campaign credits |
| /squadron_members | POST | name: string | List squadron members |
| /squadrons | GET | [limit: int], [offset: int] | List all squadrons and their roles |
| /stats | POST | nick: string, [date: string | None], [server_name: string | None], [last_session: bool | None] | Get player statistics |
| /topkdr | GET | [limit: int], [offset: int], [server_name: string] | Get top KDR statistics for players |
| /topkills | GET | [limit: int], [offset: int], [server_name: string] | Get top kills statistics for players |
| /traps | POST | nick: string, [date: string | None], [limit: int | None], [offset: int | None], [server_name: string | None] | Get traps for players |
| /traps/img | GET | trap_id: int | Get trap image for a player |
| /trueskill | GET | [limit: int], [offset: int], [server_name: string] | Get TrueSkill:tm: statistics for players |
| /weaponpk | POST | nick: string, [date: string | None], [server_name: string | None] | Get PK statistics for all weapons of a specific players |

---

## Airbase

### `GET` /airbases

**Summary:** Airbases Listing

**Description:** Get a listing of all airbases on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `AirbasesResponse`

#### Response Example

```json
{
  "airbases": [
    {
      "alt": 250.00025,
      "coalition": 1,
      "code": "ICAO",
      "dynamic": {
        "allowHotSpawn": false,
        "dynamicSpawnAvailable": true
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
      "id": "Airbase_Name",
      "lat": 35.732306452624,
      "lng": 37.104127964423,
      "name": "Airbase Name",
      "position": {
        "x": 76048.957031,
        "y": 250.00025,
        "z": 111344.925781
      },
      "runwayList": [
        "09",
        "27"
      ],
      "rwy_heading": 274
    }
  ]
}
```

---

### `GET` /airbase

**Summary:** Airbase Information

**Description:** Get information for a given airbase on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |
| `airbase_name` | `string` | query | **Yes** | - | Airbase Name |

**Response:** `AirbaseInfoResponse`

#### Response Example

```json
{
  "airbase": {
    "alt": 69.475785112923,
    "auto_capture": true,
    "channel": "...",
    "coalition": 2,
    "command": "getAirbase",
    "lat": 36.371269972814,
    "lng": 36.298090184913,
    "magVar": 5.6234159795293,
    "mgrs": "37 S BA 56617 27553",
    "name": "Airbase Name",
    "parking": [
      {
        "TO_AC": false,
        "Term_Index": 9,
        "Term_Index_0": -1,
        "Term_Type": 104,
        "fDistToRW": 1641.8400878906,
        "vTerminalPos": {
          "x": 147715.125,
          "y": 69.475784301758,
          "z": 38939.109375
        }
      }
    ],
    "position": {
      "x": 148653.765625,
      "y": 69.475784301758,
      "z": 40403.9453125
    },
    "radio_silent": true,
    "runways": [
      {
        "Name": 22,
        "course": 2.3682391643524,
        "length": 2759.2866210938,
        "position": {
          "x": 147687.484375,
          "y": 69.475784301758,
          "z": 39418.7421875
        },
        "width": 60
      }
    ],
    "server_name": "Server Name",
    "unlimited": {
      "aircraft": false,
      "liquids": true,
      "weapon": false
    },
    "warehouse": {
      "aircraft": {
        "A-10C_2": 1,
        "CH-47Fbl1": 1,
        "F-14B": 1,
        "OH58D": 1
      },
      "liquids": {
        "0": 324730.28125,
        "1": 500000,
        "2": 500000,
        "3": 500000
      },
      "weapon": {
        "weapons.bombs.BEER_BOMB": 50,
        "weapons.containers.LANTIRN": 1000,
        "weapons.droptanks.Spitfire_tank_1": 1000,
        "weapons.missiles.AGM_154": 50,
        "weapons.nurs.HYDRA_70_M151_M433": 100
      }
    }
  }
}
```

---

### `GET` /airbase/atis

**Summary:** Airbase ATIS

**Description:** Get ATIS information for an airbase on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |
| `airbase_name` | `string` | query | **Yes** | - | Airbase Name |

**Response:** `AirbaseAtisResponse`

#### Response Example

```json
{
  "active_runways": [
    "31L"
  ],
  "clouds": {
    "base": 8000,
    "density": 4,
    "thickness": 1000
  },
  "preset": {
    "detailNoiseMapSize": 9000,
    "layers": [
      {
        "altitudeMax": 3440,
        "altitudeMin": 2520,
        "coverage": 0.418,
        "coverageMapFactor": 0,
        "coverageMapUVOffsetX": 0,
        "coverageMapUVOffsetY": 0,
        "density": 0.506,
        "densityGrad": 1.2,
        "noiseBlur": 1.5,
        "noiseFreq": 2.088,
        "shapeFactor": 0,
        "tile": 4.336
      },
      {
        "altitudeMax": 8400,
        "altitudeMin": 7560,
        "coverage": 0.469,
        "coverageMapFactor": 0,
        "coverageMapUVOffsetX": 0,
        "coverageMapUVOffsetY": 0,
        "density": 0.572,
        "densityGrad": 1.466,
        "noiseBlur": 1.303,
        "noiseFreq": 1.857,
        "shapeFactor": 0.264,
        "tile": 2.992
      },
      {
        "altitudeMax": 10920,
        "altitudeMin": 10000,
        "coverage": 0,
        "coverageMapFactor": 0,
        "coverageMapUVOffsetX": 0,
        "coverageMapUVOffsetY": 0,
        "density": 0,
        "densityGrad": 0,
        "noiseBlur": 0.27,
        "noiseFreq": 2,
        "shapeFactor": 0,
        "tile": 1
      }
    ],
    "levelMap": "bazar/effects/clouds/cloudsMap01.png",
    "precipitationPower": -1,
    "presetAltMax": 2520,
    "presetAltMin": 1260,
    "readableName": "02 ##Two Layers Few and Scattered \nMETAR: FEW/SCT 8/10 SCT 23/24",
    "readableNameShort": "Light Scattered 2",
    "thumbnailName": "Bazar/Effects/Clouds/Thumbnails/cloud_2.png",
    "visibleInGUI": true
  },
  "qfe": {
    "pressureHPA": 1013.25,
    "pressureIN": 29.92,
    "pressureMM": 760.0
  },
  "qnh": {
    "pressureHPA": 1013.25,
    "pressureIN": 29.92,
    "pressureMM": 760.0
  },
  "temp": 15.5,
  "turbulence": "None",
  "wind": {
    "dir": 270.0,
    "speed": 5.2
  }
}
```

---

### `GET` /airbase/warehouse

**Summary:** Airbase Warehouse

**Description:** Get warehouse information for an airbase on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |
| `airbase_name` | `string` | query | **Yes** | - | Airbase Name |

**Response:** `AirbaseWarehouseResponse`

#### Response Example

```json
{
  "unlimited": {
    "aircraft": false,
    "liquids": true,
    "weapon": false
  },
  "warehouse": {
    "aircraft": {
      "A6E": 100,
      "AH-64D_BLK_II": 5,
      "F-16C_50": 1
    },
    "liquids": {
      "0": 500000,
      "1": 500000,
      "2": 500000,
      "3": 500000
    },
    "weapon": {
      "weapons.bombs.GBU_38": 100,
      "weapons.containers.F-15E_AXQ-14_DATALINK": 100,
      "weapons.droptanks.FuelTank_350L": 100,
      "weapons.missiles.AGM_154": 100,
      "weapons.nurs.HYDRA_70_M151_M433": 100
    }
  }
}
```

---

### `POST` /airbase/warehouse/item

**Summary:** Set Quantity of an Airbase Warehouse Item

**Description:** Set warehouse item quantity for an airbase on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | form | **Yes** | - | Server Name |
| `airbase_name` | `string` | form | **Yes** | - | Airbase Name |
| `item` | `string` | form | **Yes** | - | Item |
| `value` | `int` | form | **Yes** | - | Value |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `AirbaseSetWarehouseItemResponse`

#### Response Example

```json
{
  "item": "weapons.bombs.GBU_38",
  "server_name": "Server Name",
  "value": 50
}
```

---

### `POST` /airbase/capture

**Summary:** Airbase Information

**Description:** Capture the airbase.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | form | **Yes** | - | Server Name |
| `airbase_name` | `string` | form | **Yes** | - | Airbase Name |
| `coalition` | `int` | form | **Yes** | - | Coalition |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `AirbaseCaptureResponse`

#### Response Example

```json
{
  "airbase_name": "Airbase Name",
  "coalition": 0,
  "server_name": "Server Name"
}
```

---

## Info

### `GET` /serverstats

**Summary:** Server Statistics

**Description:** List the statistics of a whole group

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | No | - | Server Name |

**Response:** `ServerStats`

#### Response Example

```json
{
  "activePlayers": 50,
  "avgPlaytime": 120,
  "daily_players": [
    {
      "date": "2025-08-07T12:00:00",
      "player_count": 100
    }
  ],
  "totalDeaths": 50,
  "totalKills": 100,
  "totalPlayers": 100,
  "totalPlaytime": 3600,
  "totalPvPDeaths": 20,
  "totalPvPKills": 30,
  "totalSorties": 100
}
```

---

### `GET` /server_attendance

**Summary:** Server Attendance Statistics

**Description:** Get detailed server attendance statistics

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | No | - | Server Name |

**Response:** `ServerAttendanceStats`

#### Response Example

```json
{
  "current_players": 8,
  "daily_trend": [
    {
      "date": "2025-12-24",
      "unique_players": 15
    },
    {
      "date": "2025-12-25",
      "unique_players": 18
    }
  ],
  "discord_members_24h": 12,
  "discord_members_30d": 65,
  "discord_members_7d": 28,
  "top_missions": [
    {
      "mission_name": "Training Map",
      "playtime_hours": 1200
    },
    {
      "mission_name": "Combat Mission",
      "playtime_hours": 800
    }
  ],
  "top_modules": [
    {
      "module": "F/A-18C",
      "playtime_hours": 800,
      "total_uses": 127,
      "unique_players": 45
    }
  ],
  "top_theatres": [
    {
      "playtime_hours": 2500,
      "theatre": "Caucasus"
    },
    {
      "playtime_hours": 347,
      "theatre": "Syria"
    }
  ],
  "total_deaths": 567,
  "total_kills": 892,
  "total_playtime_hours_24h": 45.5,
  "total_playtime_hours_30d": 720.8,
  "total_playtime_hours_7d": 180.2,
  "total_pvp_deaths": 189,
  "total_pvp_kills": 234,
  "total_sorties": 1245,
  "unique_players_24h": 15,
  "unique_players_30d": 85,
  "unique_players_7d": 35
}
```

---

### `GET` /servers

**Summary:** Server list

**Description:** List all servers, the active mission (if any) and the active extensions

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string | None` | query | No | - | Server Name |

**Response:** `list[ServerInfo]`

#### Response Example

```json
[
  {
    "address": "127.0.0.1:10308",
    "description": "Public Dedicated Server",
    "extensions": [
      {
        "name": "SRS",
        "value": "127.0.0.1:5002",
        "version": "1.9.0.0"
      }
    ],
    "max_players": 32,
    "mission": {
      "blue_slots": 20,
      "blue_slots_used": 5,
      "date_time": "2025-08-07 12:00:00",
      "name": "Training Mission",
      "red_slots": 20,
      "red_slots_used": 3,
      "restart_time": 1691424000,
      "theatre": "Caucasus",
      "uptime": 3600
    },
    "name": "DCS Server",
    "password": "secret",
    "players": [
      {
        "callsign": "Chevy 1-1",
        "nick": "Pilot1",
        "radios": [
          127500000,
          251000000
        ],
        "side": "blue",
        "unit_type": "FA-18C_hornet"
      }
    ],
    "require_pure_clients": true,
    "require_pure_models": true,
    "require_pure_scripts": true,
    "require_pure_textures": true,
    "restart_time": "2025-08-07T12:00:00",
    "status": "running",
    "weather": {
      "clouds_base": 8000,
      "clouds_density": 4,
      "clouds_thickness": 1000,
      "dust_enabled": false,
      "fog_enabled": false,
      "precipitation": 0,
      "pressure": 760.0,
      "temperature": 15.5,
      "visibility": 9999,
      "wind_direction": 270,
      "wind_speed": 5.2
    }
  }
]
```

---

### `GET` /squadrons

**Summary:** Squadron list

**Description:** List all squadrons and their roles

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `limit` | `int` | query | No | - | Limit |
| `offset` | `int` | query | No | `0` | Offset |

**Response:** `list[SquadronInfo]`

#### Response Example

```json
[
  {
    "description": "Elite Fighter Squadron",
    "image_url": "https://example.com/squadron-logo.png",
    "locked": true,
    "members": [
      {
        "current_server": "My Fancy Server",
        "date": "2025-08-07T12:00:00",
        "discord_id": 123456789012345678,
        "nick": "Player1",
        "ucid": "aabbccddeeffgghhiiffkk1234567890"
      }
    ],
    "name": "Red Devils",
    "role": "Squadron Leader"
  }
]
```

---

### `POST` /squadron_members

**Summary:** Squadron Members

**Description:** List squadron members

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `name` | `string` | form | **Yes** | - | Name |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `list[UserEntry]`

#### Response Example

```json
[
  {
    "current_server": "My Fancy Server",
    "date": "2025-08-07T12:00:00",
    "discord_id": 123456789012345678,
    "nick": "Player1",
    "ucid": "aabbccddeeffgghhiiffkk1234567890"
  }
]
```

---

### `POST` /getuser

**Summary:** User list

**Description:** Get users by name

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `nick` | `string` | form | No | - | Nick |
| `discord_id` | `string` | form | No | - | Discord Id |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `list[UserEntry]`

#### Response Example

```json
[
  {
    "current_server": "My Fancy Server",
    "date": "2025-08-07T12:00:00",
    "discord_id": 123456789012345678,
    "nick": "Player1",
    "ucid": "aabbccddeeffgghhiiffkk1234567890"
  }
]
```

---

### `POST` /linkme

**Summary:** Link Discord to DCS

**Description:** Link your Discord account to your DCS account

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `discord_id` | `string` | form | **Yes** | - | Discord user ID (snowflake) |
| `force` | `bool` | form | No | `False` | Force the operation |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `LinkMeResponse`

#### Response Example

```json
{
  "rc": 2,
  "timestamp": "2025-08-09T12:00:00+00:00",
  "token": "1234"
}
```

---

### `GET` /current_server

**Summary:** Current Server

**Description:** Server name a player is flying on

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `nick` | `string` | query | **Yes** | - | Nick |
| `date` | `string | None` | query | No | - | Date |

**Response:** `string | None`

#### Response Example

```json
"DCS Server"
```

---

### `POST` /player_squadrons

**Summary:** Player Squadrons

**Description:** List of player squadrons

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `nick` | `string` | form | **Yes** | - | Nick |
| `date` | `string | None` | form | No | - | Date |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `list[PlayerSquadron]`

#### Response Example

```json
[
  {
    "image_url": "https://example.com/squadron-logo.png",
    "name": "Red Devils"
  }
]
```

---

## Statistics

### `GET` /leaderboard

**Summary:** Leaderboard

**Description:** Get leaderbord information

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `what` | `string` | query | **Yes** | - | What |
| `order` | `string` | query | No | `desc` | Order |
| `query` | `string | None` | query | No | - | Query |
| `limit` | `int | None` | query | No | `10` | Limit |
| `offset` | `int | None` | query | No | `0` | Offset |
| `server_name` | `string | None` | query | No | - | Server Name |

**Response:** `LeaderBoard`

#### Response Example

```json
{
  "items": [
    {
      "credits": 1500,
      "date": "2025-01-01T00:00:00",
      "deaths": 2,
      "deaths_pvp": 0,
      "kdr": 5.0,
      "kdr_pvp": 5.0,
      "kills": 10,
      "kills_pvp": 5,
      "nick": "Special K",
      "playtime": 7200,
      "row_num": 1
    }
  ],
  "offset": 0,
  "total_count": 1
}
```

---

### `GET` /topkills

**Summary:** Top Kills

**Description:** Get top kills statistics for players

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `limit` | `int` | query | No | `10` | Limit |
| `offset` | `int` | query | No | `0` | Offset |
| `server_name` | `string` | query | No | - | Server Name |

**Response:** `list[TopKill]`

#### Response Example

```json
[
  {
    "credits": 1500,
    "date": "2025-01-01T00:00:00",
    "deaths": 2,
    "deaths_pvp": 0,
    "kdr": 5.0,
    "kdr_pvp": 5.0,
    "kills": 10,
    "kills_pvp": 5,
    "nick": "Special K",
    "playtime": 7200,
    "row_num": 1
  }
]
```

---

### `GET` /topkdr

**Summary:** Top KDR

**Description:** Get top KDR statistics for players

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `limit` | `int` | query | No | `10` | Limit |
| `offset` | `int` | query | No | `0` | Offset |
| `server_name` | `string` | query | No | - | Server Name |

**Response:** `list[TopKill]`

#### Response Example

```json
[
  {
    "credits": 1500,
    "date": "2025-01-01T00:00:00",
    "deaths": 2,
    "deaths_pvp": 0,
    "kdr": 5.0,
    "kdr_pvp": 5.0,
    "kills": 10,
    "kills_pvp": 5,
    "nick": "Special K",
    "playtime": 7200,
    "row_num": 1
  }
]
```

---

### `GET` /trueskill

**Summary:** TrueSkill:tm:

**Description:** Get TrueSkill:tm: statistics for players

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `limit` | `int` | query | No | `10` | Limit |
| `offset` | `int` | query | No | `0` | Offset |
| `server_name` | `string` | query | No | - | Server Name |

**Response:** `list[Trueskill]`

#### Response Example

```json
[
  {
    "TrueSkill": 18.6,
    "date": "2025-01-01T00:00:00",
    "deaths_pvp": 2,
    "kills_pvp": 10,
    "nick": "Special K"
  }
]
```

---

### `POST` /weaponpk

**Summary:** Weapon PK

**Description:** Get PK statistics for all weapons of a specific players

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `nick` | `string` | form | **Yes** | - | Nick |
| `date` | `string | None` | form | No | - | Date |
| `server_name` | `string | None` | form | No | - | Server Name |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `list[WeaponPK]`

#### Response Example

```json
[
  {
    "hits": 10,
    "pk": 0.5,
    "shots": 20,
    "weapon": "AIM-120C"
  }
]
```

---

### `POST` /stats

**Summary:** Player Statistics

**Description:** Get player statistics

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `nick` | `string` | form | **Yes** | - | Nick |
| `date` | `string | None` | form | No | - | Date |
| `server_name` | `string | None` | form | No | - | Server Name |
| `last_session` | `bool | None` | form | No | `False` | Last Session |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `PlayerStats`

#### Response Example

```json
{
  "crashes": 15,
  "deaths": 20,
  "deaths_ground": 2,
  "deaths_helicopters": 2,
  "deaths_planes": 10,
  "deaths_pvp": 20,
  "deaths_sams": 5,
  "deaths_ships": 1,
  "ejections": 5,
  "kdr": 2.5,
  "kdrByModule": [
    {
      "kdr": 2.5,
      "module": "F/A-18C"
    }
  ],
  "kdr_pvp": 2.5,
  "kills": 100,
  "killsByModule": [
    {
      "kills": 30,
      "module": "F/A-18C"
    }
  ],
  "kills_ground": 30,
  "kills_helicopters": 10,
  "kills_planes": 40,
  "kills_pvp": 50,
  "kills_sams": 15,
  "kills_ships": 5,
  "landings": 180,
  "playtime": 3600,
  "takeoffs": 200,
  "teamkills": 2
}
```

---

### `POST` /modulestats

**Summary:** Module Statistics

**Description:** Get module statistics

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `nick` | `string` | form | **Yes** | - | Nick |
| `date` | `string | None` | form | No | - | Date |
| `server_name` | `string | None` | form | No | - | Server Name |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `list[ModuleStats]`

#### Response Example

```json
[
  {
    "deaths": 10,
    "kdr": 3.0,
    "kills": 30,
    "module": "F/A-18C",
    "playtime": 3600
  }
]
```

---

### `POST` /player_info

**Summary:** Player Information

**Description:** Get player information

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `nick` | `string` | form | **Yes** | - | Nick |
| `date` | `string | None` | form | No | - | Date |
| `server_name` | `string | None` | form | No | - | Server Name |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `PlayerInfo`

#### Response Example

```json
{
  "credits": {
    "badge": "https://example.com/rookie_badge.png",
    "credits": 1500.0,
    "id": 1,
    "name": "Summer Campaign 2025",
    "rank": "Rookie"
  },
  "current_server": "DCS Server",
  "last_session": {
    "crashes": 0,
    "deaths": 2,
    "deaths_ground": 0,
    "deaths_helicopters": 0,
    "deaths_planes": 1,
    "deaths_pvp": 1,
    "deaths_sams": 1,
    "deaths_ships": 0,
    "ejections": 0,
    "kdr": 5.0,
    "kdrByModule": [
      {
        "kdr": 5.0,
        "module": "F/A-18C"
      }
    ],
    "kdr_pvp": 5.0,
    "kills": 10,
    "killsByModule": [
      {
        "kills": 10,
        "module": "F/A-18C"
      }
    ],
    "kills_ground": 3,
    "kills_helicopters": 1,
    "kills_planes": 4,
    "kills_pvp": 5,
    "kills_sams": 2,
    "kills_ships": 0,
    "landings": 3,
    "playtime": 3600,
    "takeoffs": 3,
    "teamkills": 0
  },
  "module_stats": [
    {
      "deaths": 10,
      "kdr": 3.0,
      "kills": 30,
      "module": "F/A-18C",
      "playtime": 3600
    }
  ],
  "overall": {
    "crashes": 22,
    "deaths": 30,
    "deaths_ground": 3,
    "deaths_helicopters": 3,
    "deaths_planes": 15,
    "deaths_pvp": 25,
    "deaths_sams": 8,
    "deaths_ships": 1,
    "ejections": 8,
    "kdr": 5.0,
    "kdrByModule": [
      {
        "kdr": 3.5,
        "module": "F/A-18C"
      }
    ],
    "kdr_pvp": 3.2,
    "kills": 150,
    "killsByModule": [
      {
        "kills": 50,
        "module": "F/A-18C"
      }
    ],
    "kills_ground": 50,
    "kills_helicopters": 15,
    "kills_planes": 60,
    "kills_pvp": 80,
    "kills_sams": 20,
    "kills_ships": 5,
    "landings": 270,
    "playtime": 7200,
    "takeoffs": 300,
    "teamkills": 1
  },
  "squadrons": [
    {
      "image_url": "https://example.com/squadron-logo.png",
      "name": "Red Devils"
    }
  ]
}
```

---

### `GET` /highscore

**Summary:** Highscore

**Description:** Get highscore statistics for players

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | No | - | Server Name |
| `period` | `string` | query | No | `all` | Period |
| `limit` | `int` | query | No | `10` | Limit |

**Response:** `Highscore`

#### Response Example

```json
{
  "Air Defence": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 12.0
    }
  ],
  "Air Targets": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 42.0
    }
  ],
  "Ground Targets": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 55.0
    }
  ],
  "KD-Ratio": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 3.5
    }
  ],
  "Most Efficient Killers": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 15.2
    }
  ],
  "Most Wasteful Pilots": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 1.1
    }
  ],
  "PvP-KD-Ratio": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 2.5
    }
  ],
  "Ships": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 3.0
    }
  ],
  "playtime": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "playtime": 3600
    }
  ]
}
```

---

### `POST` /traps

**Summary:** Carrier Traps

**Description:** Get traps for players

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `nick` | `string` | form | **Yes** | - | Nick |
| `date` | `string | None` | form | No | - | Date |
| `limit` | `int | None` | form | No | `10` | Limit |
| `offset` | `int | None` | form | No | `0` | Offset |
| `server_name` | `string | None` | form | No | - | Server Name |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `list[TrapEntry]`

#### Response Example

```json
[
  {
    "comment": "Good pass",
    "grade": "OK",
    "id": 1,
    "night": false,
    "place": "CVN-73",
    "points": 100,
    "time": "2025-08-07T12:00:00",
    "trapcase": 3,
    "unit_type": "F/A-18C",
    "wire": 3
  }
]
```

---

### `GET` /traps/img

**Summary:** Carrier Trap Image

**Description:** Get trap image for a player

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `trap_id` | `int` | query | **Yes** | - | Trap Id |

**Response:** `string`

#### Response Example

*Binary PNG image stream (`image/png`)*

---

### `POST` /greenieboard

**Summary:** GreenieBoard

**Description:** Get a greenieboard

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `date` | `string | None` | form | No | - | Date |
| `server_name` | `string | None` | form | No | - | Server Name |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `GreenieboardResponse`

#### Response Example

```json
{
  "players": [
    {
      "nick": "Player1",
      "traps": [
        {
          "comment": "Good pass",
          "grade": "OK",
          "id": 1,
          "night": false,
          "place": "CVN-73",
          "points": 100,
          "time": "2025-08-07T12:00:00",
          "trapcase": 3,
          "unit_type": "F/A-18C",
          "wire": 3
        }
      ]
    }
  ]
}
```

---

### `GET` /events

**Summary:** Mission Events

**Description:** Get mission events for players

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `ucid` | `string` | query | **Yes** | - | Ucid |
| `start_time` | `datetime` | query | **Yes** | - | Start Time |
| `end_time` | `datetime` | query | **Yes** | - | End Time |
| `event` | `string | None` | query | No | - | Event |
| `init_type` | `string | None` | query | No | - | Init Type |
| `offset` | `int | None` | query | No | `0` | Offset |
| `limit` | `int | None` | query | No | `10` | Limit |

**Response:** `list[EventEntry]`

#### Response Example

```json
[
  {
    "comment": "First kill of the day!",
    "event": "S_EVENT_KILL",
    "init_cat": "Airplanes",
    "init_id": "aabbccddeeffgghhiiffkk1234567890",
    "init_side": 2,
    "init_type": "FA-18C_hornet",
    "mission_id": 1,
    "place": "Over the Caucasus",
    "target_cat": "Airplanes",
    "target_id": "11223344556677889900aabbccddeeff",
    "target_side": 1,
    "target_type": "MiG-29A",
    "time": "2025-08-07T12:00:00",
    "weapon": "AIM-120C"
  }
]
```

---

## Credits

### `POST` /credits

**Summary:** Campaign Credits

**Description:** Get campaign credits for players

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `nick` | `string` | form | **Yes** | - | Nick |
| `date` | `string | None` | form | No | - | Date |
| `campaign` | `string | None` | form | No | - | Campaign |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `CampaignCredits`

#### Response Example

```json
{
  "badge": "https://example.com/rookie_badge.png",
  "credits": 1500.0,
  "id": 1,
  "name": "Summer Campaign 2025",
  "rank": "Rookie"
}
```

---

### `POST` /squadron_credits

**Summary:** Squadron Credits

**Description:** Squadron campaign credits

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `name` | `string` | form | **Yes** | - | Name |
| `campaign` | `string` | form | No | - | Campaign |

**Request Body Content-Type:** `application/x-www-form-urlencoded`

**Response:** `SquadronCampaignCredit`

#### Response Example

```json
{
  "campaign": "Summer Campaign 2025",
  "credits": 1500.0
}
```

---

## Utilities

### `GET` /convertCoordinates

**Summary:** Converts the provided coordinate into multiple formats.

**Description:** Convert provided coordinate string into other formats.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |
| `coordinates` | `string` | query | **Yes** | - | Coordinates |

**Response:** `ConvertCoordinates`

#### Response Example

```json
{
  "ddm": "N35°24.33333 E35°56.93333",
  "dms": "N 35°24'20.00\" E 035°56'56.00\"",
  "latlon": "35.40556, 35.94889",
  "meters": {
    "x": 42430,
    "y": 5719
  },
  "mgrs": "36S YE 67795 22013"
}
```

---

### `GET` /mission/group/waypoints

**Summary:** Group Waypoints

**Description:** Get the lat/lon waypoints for a named group in the current mission.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Name of the server |
| `group_name` | `string` | query | **Yes** | - | Name of the group to retrieve waypoints for |
| `group_type` | `string` | query | **Yes** | - | Category of the group |

**Response:** `GroupWaypointsResponse`

#### Response Example

```json
{
  "group_name": "Tanker-1",
  "group_type": "plane",
  "waypoints": {
    "wp1": {
      "lat": 36.00001,
      "lon": 36.00001
    },
    "wp2": {
      "lat": 36.5,
      "lon": 36.5
    }
  }
}
```

---

### `GET` /mission/bullseyes

**Summary:** Mission Bullseyes

**Description:** Get the bullseye coordinates for blue and red coalitions in the current mission.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Name of the server |

**Response:** `MissionBullseyesResponse`

#### Response Example

```json
{
  "bullseyes": [
    {
      "coalition": "blue",
      "lat": 36.12345,
      "lng": 36.54321
    },
    {
      "coalition": "red",
      "lat": 35.98765,
      "lng": 35.45678
    }
  ]
}
```

---

### `GET` /mission/drawings

**Summary:** Mission Drawings

**Description:** Get mission drawing objects grouped by drawing layer.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Name of the server |

**Response:** `MissionDrawingsResponse`

#### Response Example

```json
{
  "drawings": {
    "Layer 1": [
      {
        "location": {
          "lat": 36.12345,
          "lng": 36.54321
        },
        "name": "AO Boundary",
        "points": [
          {
            "lat": 36.1,
            "lng": 36.5
          },
          {
            "lat": 36.2,
            "lng": 36.6
          }
        ],
        "primitiveType": "Line"
      }
    ]
  }
}
```

---

### `GET` /mission/unit

**Summary:** Mission Unit

**Description:** Get mission unit data including current position, loadout, navaids, and waypoints.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Name of the server |
| `unit_name` | `string` | query | **Yes** | - | Name of the unit to retrieve |

**Response:** `MissionUnitResponse`

#### Response Example

```json
{
  "current_location": {
    "alt": 58.6,
    "lat": 13.58402,
    "lon": 144.93082
  },
  "fuel_percentage": 0.85,
  "group_name": "Andersen AFB Group",
  "icls": {
    "active": false
  },
  "in_air": false,
  "life": 100.0,
  "loadout": {
    "AIM-120C": {
      "count": 2,
      "displayName": "AIM-120C AMRAAM"
    }
  },
  "player_name": "PilotNick",
  "speed": 0.0,
  "tacan": {
    "active": true,
    "channel": 67,
    "modeChannel": "X"
  },
  "type": "FA-18C_hornet",
  "unit_name": "Andersen AFB_F/A-18C Lot 20_0-1",
  "waypoints": [
    {
      "alt": 3000.0,
      "lat": 13.6,
      "lng": 145.0,
      "speed": 180.0
    }
  ]
}
```

---

## Instance Control

### `POST` /instance/start

**Summary:** Start a server instance.

**Description:** Start a server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `ServerStartResponse`

#### Response Example

```json
{
  "message": "Server 'DCS Server' started.",
  "status": "success"
}
```

---

### `POST` /instance/stop

**Summary:** Stop a server instance.

**Description:** Stop a server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `ServerStopResponse`

#### Response Example

```json
{
  "message": "Server 'DCS Server' stopped.",
  "status": "success"
}
```

---

### `POST` /instance/restart

**Summary:** Restart a server instance.

**Description:** Restart a server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `ServerRestartResponse`

#### Response Example

```json
{
  "message": "Server 'DCS Server' restarted.",
  "status": "success"
}
```

---

## Mission Control

### `GET` /instance/missions

**Summary:** Mission listing for a server instance.

**Description:** Return all missions for a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `MissionsResponse`

#### Response Example

```json
{
  "count": 2,
  "missions": [
    {
      "installed": true,
      "name": "Training Mission",
      "path": "Training Mission.miz"
    },
    {
      "installed": false,
      "name": "Combat Scenario",
      "path": "scenarios/Combat Scenario.miz"
    }
  ]
}
```

---

### `POST` /instance/mission/pause

**Summary:** Pause mission for a server instance.

**Description:** Pause the mission on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `MissionPauseResponse`

#### Response Example

```json
{
  "message": "Mission on server 'DCS Server' paused.",
  "status": "success"
}
```

---

### `POST` /instance/mission/unpause

**Summary:** Unpause mission for a server instance.

**Description:** Unpause the mission on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `MissionUnpauseResponse`

#### Response Example

```json
{
  "message": "Mission on server 'DCS Server' resumed.",
  "status": "success"
}
```

---

### `POST` /instance/mission/restart

**Summary:** Restart mission for a server instance.

**Description:** Restart the mission on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `MissionRestartResponse`

#### Response Example

```json
{
  "message": "Mission on server 'DCS Server' restarted.",
  "status": "success"
}
```

---

### `POST` /instance/mission/load

**Summary:** Load mission for a server instance.

**Description:** Load a mission on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |
| `mission_name` | `string` | query | **Yes** | - | Mission Name |

**Response:** `MissionLoadResponse`

#### Response Example

```json
{
  "message": "Mission 'Training Mission.miz' loaded on server 'DCS Server'.",
  "status": "success"
}
```

---

## Mission

### `POST` /mission/upload

**Summary:** Upload mission file

**Description:** Upload a .miz mission file to the server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Name of the server to upload the mission to |
| `file` | `string` | form | **Yes** | - | Mission file (.miz) |
| `filename` | `string` | form | **Yes** | - | Filename for the mission file (required) |
| `load_after` | `bool` | form | **Yes** | - | Load mission after upload (default: False) |

**Request Body Content-Type:** `multipart/form-data`

**Response:** `MissionUploadResponse`

#### Response Example

```json
{
  "message": "Mission 'Training Mission.miz' uploaded to server 'DCS Server'.",
  "status": "success"
}
```

---

## Schema Models Reference

Below are the data structures and response models used across the API endpoints:

### `Airbase`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `alt` | `float` | **Yes** | Alt |
| `code` | `string | None` | No | Code |
| `id` | `string | None` | No | Id |
| `lat` | `float` | **Yes** | Lat |
| `rwy_heading` | `int | None` | No | Rwy Heading |
| `lng` | `float` | **Yes** | Lng |
| `name` | `string` | **Yes** | Name |
| `position` | `Position` | **Yes** | - |
| `frequencyList` | `list[list[int]] | list[list[any]] | object | None` | No | Frequencylist |
| `dynamic` | `Dynamic` | **Yes** | - |
| `runwayList` | `list[string] | object | None` | No | Runwaylist |
| `coalition` | `string | int | None` | No | Coalition |

#### Example

```json
{
  "alt": 250.0,
  "coalition": 2,
  "code": "UGSB",
  "dynamic": {
    "allowHotSpawn": false,
    "dynamicSpawnAvailable": true
  },
  "frequencyList": [
    [
      131000000,
      0
    ],
    [
      260000000,
      0
    ]
  ],
  "id": "Batumi",
  "lat": 41.6166,
  "lng": 41.6,
  "name": "Batumi",
  "position": {
    "x": 76048.95,
    "y": 250.0,
    "z": 111344.92
  },
  "runwayList": [
    "13",
    "31"
  ],
  "rwy_heading": 126
}
```

### `AirbaseAtisResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `temp` | `float` | **Yes** | Temperature in Celsius |
| `qfe` | `PressureInfo | object` | **Yes** | QFE pressure |
| `qnh` | `PressureInfo | object` | **Yes** | QNH pressure |
| `turbulence` | `float | None` | No | Turbulence in kts |
| `wind` | `WindInfo | object` | **Yes** | Wind conditions in kts |
| `preset` | `object | None` | No | Weather preset |
| `clouds` | `object | None` | No | Cloud cover information |
| `active_runways` | `list[any] | None` | No | Active Runways |

#### Example

```json
{
  "active_runways": [
    "31L"
  ],
  "clouds": {
    "base": 8000,
    "density": 4,
    "thickness": 1000
  },
  "preset": {
    "detailNoiseMapSize": 9000,
    "layers": [
      {
        "altitudeMax": 3440,
        "altitudeMin": 2520,
        "coverage": 0.418,
        "coverageMapFactor": 0,
        "coverageMapUVOffsetX": 0,
        "coverageMapUVOffsetY": 0,
        "density": 0.506,
        "densityGrad": 1.2,
        "noiseBlur": 1.5,
        "noiseFreq": 2.088,
        "shapeFactor": 0,
        "tile": 4.336
      },
      {
        "altitudeMax": 8400,
        "altitudeMin": 7560,
        "coverage": 0.469,
        "coverageMapFactor": 0,
        "coverageMapUVOffsetX": 0,
        "coverageMapUVOffsetY": 0,
        "density": 0.572,
        "densityGrad": 1.466,
        "noiseBlur": 1.303,
        "noiseFreq": 1.857,
        "shapeFactor": 0.264,
        "tile": 2.992
      },
      {
        "altitudeMax": 10920,
        "altitudeMin": 10000,
        "coverage": 0,
        "coverageMapFactor": 0,
        "coverageMapUVOffsetX": 0,
        "coverageMapUVOffsetY": 0,
        "density": 0,
        "densityGrad": 0,
        "noiseBlur": 0.27,
        "noiseFreq": 2,
        "shapeFactor": 0,
        "tile": 1
      }
    ],
    "levelMap": "bazar/effects/clouds/cloudsMap01.png",
    "precipitationPower": -1,
    "presetAltMax": 2520,
    "presetAltMin": 1260,
    "readableName": "02 ##Two Layers Few and Scattered \nMETAR: FEW/SCT 8/10 SCT 23/24",
    "readableNameShort": "Light Scattered 2",
    "thumbnailName": "Bazar/Effects/Clouds/Thumbnails/cloud_2.png",
    "visibleInGUI": true
  },
  "qfe": {
    "pressureHPA": 1013.25,
    "pressureIN": 29.92,
    "pressureMM": 760.0
  },
  "qnh": {
    "pressureHPA": 1013.25,
    "pressureIN": 29.92,
    "pressureMM": 760.0
  },
  "temp": 15.5,
  "turbulence": "None",
  "wind": {
    "dir": 270.0,
    "speed": 5.2
  }
}
```

### `AirbaseCaptureResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `server_name` | `string` | **Yes** | Server name |
| `airbase_name` | `string` | **Yes** | Airbase name |
| `coalition` | `int` | **Yes** | Coalition capturing the airbase |

#### Example

```json
{
  "airbase_name": "Airbase Name",
  "coalition": 0,
  "server_name": "Server Name"
}
```

### `AirbaseInfoResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `airbase` | `object` | **Yes** | Airbase data |

#### Example

```json
{
  "airbase": {
    "alt": 69.475785112923,
    "auto_capture": true,
    "channel": "...",
    "coalition": 2,
    "command": "getAirbase",
    "lat": 36.371269972814,
    "lng": 36.298090184913,
    "magVar": 5.6234159795293,
    "mgrs": "37 S BA 56617 27553",
    "name": "Airbase Name",
    "parking": [
      {
        "TO_AC": false,
        "Term_Index": 9,
        "Term_Index_0": -1,
        "Term_Type": 104,
        "fDistToRW": 1641.8400878906,
        "vTerminalPos": {
          "x": 147715.125,
          "y": 69.475784301758,
          "z": 38939.109375
        }
      }
    ],
    "position": {
      "x": 148653.765625,
      "y": 69.475784301758,
      "z": 40403.9453125
    },
    "radio_silent": true,
    "runways": [
      {
        "Name": 22,
        "course": 2.3682391643524,
        "length": 2759.2866210938,
        "position": {
          "x": 147687.484375,
          "y": 69.475784301758,
          "z": 39418.7421875
        },
        "width": 60
      }
    ],
    "server_name": "Server Name",
    "unlimited": {
      "aircraft": false,
      "liquids": true,
      "weapon": false
    },
    "warehouse": {
      "aircraft": {
        "A-10C_2": 1,
        "CH-47Fbl1": 1,
        "F-14B": 1,
        "OH58D": 1
      },
      "liquids": {
        "0": 324730.28125,
        "1": 500000,
        "2": 500000,
        "3": 500000
      },
      "weapon": {
        "weapons.bombs.BEER_BOMB": 50,
        "weapons.containers.LANTIRN": 1000,
        "weapons.droptanks.Spitfire_tank_1": 1000,
        "weapons.missiles.AGM_154": 50,
        "weapons.nurs.HYDRA_70_M151_M433": 100
      }
    }
  }
}
```

### `AirbaseSetWarehouseItemResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `item` | `string` | **Yes** | Warehouse item name |
| `server_name` | `string` | **Yes** | Server name |
| `value` | `int` | **Yes** | Quantity value |

#### Example

```json
{
  "item": "weapons.bombs.GBU_38",
  "server_name": "Server Name",
  "value": 50
}
```

### `AirbaseWarehouseResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `warehouse` | `object` | **Yes** | Warehouse data |
| `unlimited` | `object` | **Yes** | Unlimited flags |

#### Example

```json
{
  "unlimited": {
    "aircraft": false,
    "liquids": true,
    "weapon": false
  },
  "warehouse": {
    "aircraft": {
      "A6E": 100,
      "AH-64D_BLK_II": 5,
      "F-16C_50": 1
    },
    "liquids": {
      "0": 500000,
      "1": 500000,
      "2": 500000,
      "3": 500000
    },
    "weapon": {
      "weapons.bombs.GBU_38": 100,
      "weapons.containers.F-15E_AXQ-14_DATALINK": 100,
      "weapons.droptanks.FuelTank_350L": 100,
      "weapons.missiles.AGM_154": 100,
      "weapons.nurs.HYDRA_70_M151_M433": 100
    }
  }
}
```

### `AirbasesResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `airbases` | `list[Airbase]` | **Yes** | Airbases data |

#### Example

```json
{
  "airbases": [
    {
      "alt": 250.00025,
      "coalition": 1,
      "code": "ICAO",
      "dynamic": {
        "allowHotSpawn": false,
        "dynamicSpawnAvailable": true
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
      "id": "Airbase_Name",
      "lat": 35.732306452624,
      "lng": 37.104127964423,
      "name": "Airbase Name",
      "position": {
        "x": 76048.957031,
        "y": 250.00025,
        "z": 111344.925781
      },
      "runwayList": [
        "09",
        "27"
      ],
      "rwy_heading": 274
    }
  ]
}
```

### `CampaignCredits`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `int` | **Yes** | Campaign ID |
| `name` | `string` | **Yes** | Campaign name |
| `credits` | `float` | **Yes** | Player's credits in this campaign |
| `rank` | `string | None` | **Yes** | Player's rank |
| `badge` | `string | None` | **Yes** | Player's badge |

#### Example

```json
{
  "badge": "https://example.com/rookie_badge.png",
  "credits": 1500.0,
  "id": 1,
  "name": "Summer Campaign 2025",
  "rank": "Rookie"
}
```

### `ConvertCoordinates`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `latlon` | `string` | **Yes** | Latitude and Longitude in decimal degrees |
| `mgrs` | `string` | **Yes** | Cooridnate provided, converted to MGRS |
| `dms` | `string` | **Yes** | Cooridnate provided, converted to Decimal, Minutes, Seconds |
| `ddm` | `string` | **Yes** | Cooridnate provided, converted to Degrees and Decimal Minutes |
| `meters` | `object` | **Yes** | Cooridnate provided, converted to DCS Meters |

#### Example

```json
{
  "ddm": "N35°24.33333 E35°56.93333",
  "dms": "N 35°24'20.00\" E 035°56'56.00\"",
  "latlon": "35.40556, 35.94889",
  "meters": {
    "x": 42430,
    "y": 5719
  },
  "mgrs": "36S YE 67795 22013"
}
```

### `DailyPlayers`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `date` | `datetime` | **Yes** | Date |
| `player_count` | `int` | **Yes** | Player Count |

#### Example

```json
{
  "date": "2025-08-07T12:00:00",
  "player_count": 100
}
```

### `Dynamic`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `dynamicSpawnAvailable` | `bool` | **Yes** | Dynamicspawnavailable |
| `allowHotSpawn` | `bool` | **Yes** | Allowhotspawn |

#### Example

```json
{
  "allowHotSpawn": false,
  "dynamicSpawnAvailable": true
}
```

### `EventEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `mission_id` | `int` | **Yes** | Mission ID |
| `event` | `string` | **Yes** | Event type |
| `init_id` | `string | None` | No | Initiator UCID |
| `init_side` | `int | None` | No | Initiator side |
| `init_type` | `string | None` | No | Initiator type |
| `init_cat` | `string | None` | No | Initiator category |
| `target_id` | `string | None` | No | Target UCID |
| `target_side` | `int | None` | No | Target side |
| `target_type` | `string | None` | No | Target type |
| `target_cat` | `string | None` | No | Target category |
| `weapon` | `string | None` | No | Weapon used in the event |
| `place` | `string | None` | No | Event location |
| `comment` | `string | None` | No | Event comment |
| `time` | `datetime` | **Yes** | Event time |

#### Example

```json
{
  "comment": "First kill of the day!",
  "event": "S_EVENT_KILL",
  "init_cat": "Airplanes",
  "init_id": "aabbccddeeffgghhiiffkk1234567890",
  "init_side": 2,
  "init_type": "FA-18C_hornet",
  "mission_id": 1,
  "place": "Over the Caucasus",
  "target_cat": "Airplanes",
  "target_id": "11223344556677889900aabbccddeeff",
  "target_side": 1,
  "target_type": "MiG-29A",
  "time": "2025-08-07T12:00:00",
  "weapon": "AIM-120C"
}
```

### `ExtensionInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Name |
| `version` | `string | None` | No | Version |
| `value` | `string` | **Yes** | Value |

#### Example

```json
{
  "name": "SRS",
  "value": "127.0.0.1:5002",
  "version": "1.9.0.0"
}
```

### `GreenieboardEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player name |
| `traps` | `list[TrapEntry]` | **Yes** | List of traps for this player |

#### Example

```json
{
  "nick": "Player1",
  "traps": [
    {
      "comment": "Good pass",
      "grade": "OK",
      "id": 1,
      "night": false,
      "place": "CVN-73",
      "points": 100,
      "time": "2025-08-07T12:00:00",
      "trapcase": 3,
      "unit_type": "F/A-18C",
      "wire": 3
    }
  ]
}
```

### `GreenieboardResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `players` | `list[GreenieboardEntry]` | **Yes** | All players and their traps |

#### Example

```json
{
  "players": [
    {
      "nick": "Player1",
      "traps": [
        {
          "comment": "Good pass",
          "grade": "OK",
          "id": 1,
          "night": false,
          "place": "CVN-73",
          "points": 100,
          "time": "2025-08-07T12:00:00",
          "trapcase": 3,
          "unit_type": "F/A-18C",
          "wire": 3
        }
      ]
    }
  ]
}
```

### `GroupWaypointsResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `group_name` | `string` | **Yes** | Name of the group |
| `group_type` | `string` | **Yes** | Type of the group |
| `waypoints` | `object` | **Yes** | Keyed waypoint dictionary (wp1, wp2, ...) with lat/lon per waypoint |

#### Example

```json
{
  "group_name": "Tanker-1",
  "group_type": "plane",
  "waypoints": {
    "wp1": {
      "lat": 36.00001,
      "lon": 36.00001
    },
    "wp2": {
      "lat": 36.5,
      "lon": 36.5
    }
  }
}
```

### `Highscore`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `playtime` | `list[PlaytimeEntry]` | No | Playtime rankings |
| `Air Targets` | `list[HighscoreEntry]` | No | Air kills rankings |
| `Ships` | `list[HighscoreEntry]` | No | Ship kills rankings |
| `Air Defence` | `list[HighscoreEntry]` | No | SAM kills rankings |
| `Ground Targets` | `list[HighscoreEntry]` | No | Ground kills rankings |
| `KD-Ratio` | `list[HighscoreEntry]` | No | Kill/Death ratio rankings |
| `PvP-KD-Ratio` | `list[HighscoreEntry]` | No | PvP Kill/Death ratio rankings |
| `Most Efficient Killers` | `list[HighscoreEntry]` | No | Kills per hour rankings |
| `Most Wasteful Pilots` | `list[HighscoreEntry]` | No | Crashes per hour rankings |

#### Example

```json
{
  "Air Defence": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 12.0
    }
  ],
  "Air Targets": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 42.0
    }
  ],
  "Ground Targets": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 55.0
    }
  ],
  "KD-Ratio": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 3.5
    }
  ],
  "Most Efficient Killers": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 15.2
    }
  ],
  "Most Wasteful Pilots": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 1.1
    }
  ],
  "PvP-KD-Ratio": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 2.5
    }
  ],
  "Ships": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "value": 3.0
    }
  ],
  "playtime": [
    {
      "date": "2025-08-07T12:00:00",
      "nick": "Player1",
      "playtime": 3600
    }
  ]
}
```

### `HighscoreEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player nickname |
| `date` | `datetime` | **Yes** | Last seen timestamp |
| `value` | `string` | **Yes** | Score value (varies by category) |

#### Example

```json
{
  "date": "2025-08-07T12:00:00",
  "nick": "Player1",
  "value": 42.0
}
```

### `LeaderBoard`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `items` | `list[TopKill]` | **Yes** | Items |
| `total_count` | `int` | **Yes** | Total Count |
| `offset` | `int` | **Yes** | Offset |

#### Example

```json
{
  "items": [
    {
      "credits": 1500,
      "date": "2025-01-01T00:00:00",
      "deaths": 2,
      "deaths_pvp": 0,
      "kdr": 5.0,
      "kdr_pvp": 5.0,
      "kills": 10,
      "kills_pvp": 5,
      "nick": "Special K",
      "playtime": 7200,
      "row_num": 1
    }
  ],
  "offset": 0,
  "total_count": 1
}
```

### `LinkMeResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `token` | `string | None` | No | 4-digit token for linking DCS and Discord accounts |
| `timestamp` | `string | None` | No | Expiry timestamp in ISO format |
| `rc` | `int` | **Yes** | Return code bitmask (1=User linked, 2=Link in progress, 4=Force operation) |

#### Example

```json
{
  "rc": 2,
  "timestamp": "2025-08-09T12:00:00+00:00",
  "token": "1234"
}
```

### `MissionBullseye`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `coalition` | `string` | **Yes** | Coalition name ('blue' or 'red') |
| `lat` | `float` | **Yes** | Bullseye latitude in decimal degrees |
| `lng` | `float` | **Yes** | Bullseye longitude in decimal degrees |

#### Example

```json
{
  "coalition": "blue",
  "lat": 36.12345,
  "lng": 36.54321
}
```

### `MissionBullseyesResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `bullseyes` | `list[MissionBullseye]` | **Yes** | List of coalition bullseye coordinates |

#### Example

```json
{
  "bullseyes": [
    {
      "coalition": "blue",
      "lat": 36.12345,
      "lng": 36.54321
    },
    {
      "coalition": "red",
      "lat": 35.98765,
      "lng": 35.45678
    }
  ]
}
```

### `MissionDrawing`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Drawing name |
| `primitiveType` | `string` | **Yes** | Drawing primitive type |
| `text` | `string | None` | No | Optional drawing text |
| `layerName` | `string | None` | No | Drawing layer name |
| `visible` | `bool | None` | No | Whether drawing is visible |
| `mapX` | `float | None` | No | Drawing origin X in mission meters |
| `mapY` | `float | None` | No | Drawing origin Y in mission meters |
| `colorString` | `string | None` | No | Stroke color as ARGB hex string |
| `fillColorString` | `string | None` | No | Fill color as ARGB hex string |
| `style` | `string | None` | No | Line or fill style |
| `thickness` | `float | int | None` | No | Line thickness |
| `location` | `MissionDrawingPoint | None` | No | Drawing anchor location |
| `points` | `list[MissionDrawingPoint] | None` | No | Optional list of drawing points |
| `lineMode` | `string | None` | No | Line mode (segment, segments, free) |
| `closed` | `bool | None` | No | Whether line is closed |
| `polygonMode` | `string | None` | No | Polygon mode (free, circle, oval, rect, arrow) |
| `radius` | `float | None` | No | Radius for circle/disc polygons |
| `r1` | `float | None` | No | First oval radius |
| `r2` | `float | None` | No | Second oval radius |
| `width` | `float | None` | No | Width for rectangle polygons |
| `height` | `float | None` | No | Height for rectangle polygons |
| `length` | `float | None` | No | Length for arrow polygons |
| `angle` | `float | None` | No | Drawing rotation angle |
| `font` | `string | None` | No | TextBox font file |
| `fontSize` | `float | int | None` | No | TextBox font size |
| `borderThickness` | `float | int | None` | No | TextBox border thickness |
| `file` | `string | None` | No | Icon file name |
| `scale` | `float | None` | No | Icon scale factor |

#### Example

```json
{
  "colorString": "0xFF0000FF",
  "fillColorString": "0x330000FF",
  "layerName": "Layer 1",
  "location": {
    "lat": 36.12345,
    "lng": 36.54321
  },
  "mapX": 147687.0,
  "mapY": 39418.0,
  "name": "AO Boundary",
  "points": [
    {
      "lat": 36.1,
      "lng": 36.5
    },
    {
      "lat": 36.2,
      "lng": 36.6
    }
  ],
  "primitiveType": "Line",
  "style": "solid",
  "text": "Restricted Area",
  "thickness": 2,
  "visible": true
}
```

### `MissionDrawingPoint`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `lat` | `float` | **Yes** | Latitude in decimal degrees |
| `lng` | `float` | **Yes** | Longitude in decimal degrees |

#### Example

```json
{
  "lat": 36.12345,
  "lng": 36.54321
}
```

### `MissionDrawingsResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `drawings` | `object` | **Yes** | Drawings keyed by layer name; each drawing contains primitive-specific fields |

#### Example

```json
{
  "drawings": {
    "Layer 1": [
      {
        "location": {
          "lat": 36.12345,
          "lng": 36.54321
        },
        "name": "AO Boundary",
        "points": [
          {
            "lat": 36.1,
            "lng": 36.5
          },
          {
            "lat": 36.2,
            "lng": 36.6
          }
        ],
        "primitiveType": "Line"
      }
    ]
  }
}
```

### `MissionEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Mission name without extension |
| `path` | `string` | **Yes** | Relative path to mission file |
| `installed` | `bool` | **Yes** | Whether mission is in the active mission list |

#### Example

```json
{
  "installed": true,
  "name": "Training Mission",
  "path": "Training Mission.miz"
}
```

### `MissionInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Name |
| `uptime` | `int` | **Yes** | Uptime |
| `date_time` | `string` | **Yes** | Date Time |
| `theatre` | `string` | **Yes** | Theatre |
| `blue_slots` | `int | None` | No | Blue Slots |
| `blue_slots_used` | `int | None` | No | Blue Slots Used |
| `red_slots` | `int | None` | No | Red Slots |
| `red_slots_used` | `int | None` | No | Red Slots Used |
| `restart_time` | `int | None` | No | Restart Time |

#### Example

```json
{
  "blue_slots": 20,
  "blue_slots_used": 5,
  "date_time": "2025-08-07 12:00:00",
  "name": "Training Mission",
  "red_slots": 20,
  "red_slots_used": 3,
  "restart_time": 1691424000,
  "theatre": "Caucasus",
  "uptime": 3600
}
```

### `MissionLoadResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

#### Example

```json
{
  "message": "Mission 'Training Mission.miz' loaded on server 'DCS Server'.",
  "status": "success"
}
```

### `MissionPauseResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

#### Example

```json
{
  "message": "Mission on server 'DCS Server' paused.",
  "status": "success"
}
```

### `MissionRestartResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

#### Example

```json
{
  "message": "Mission on server 'DCS Server' restarted.",
  "status": "success"
}
```

### `MissionUnitLoadoutItem`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `displayName` | `string` | **Yes** | Display name of the weapon or store |
| `count` | `int` | **Yes** | Remaining count |

#### Example

```json
{
  "count": 2,
  "displayName": "AIM-120C AMRAAM"
}
```

### `MissionUnitLocation`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `lat` | `float` | **Yes** | Current latitude in decimal degrees |
| `lon` | `float` | **Yes** | Current longitude in decimal degrees |
| `alt` | `float` | **Yes** | Current altitude in meters |

#### Example

```json
{
  "alt": 58.6,
  "lat": 13.58402,
  "lon": 144.93082
}
```

### `MissionUnitNavAid`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `active` | `bool` | **Yes** | Whether this navaid is active |
| `channel` | `int | None` | No | Configured channel if available |
| `modeChannel` | `string | int | None` | No | TACAN mode channel, if available |

#### Example

```json
{
  "active": true,
  "channel": 67,
  "modeChannel": "X"
}
```

### `MissionUnitResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `type` | `string` | **Yes** | DCS unit type name |
| `group_name` | `string` | **Yes** | DCS group name |
| `unit_name` | `string` | **Yes** | DCS unit name |
| `current_location` | `MissionUnitLocation` | **Yes** | Current unit location |
| `speed` | `float` | **Yes** | Current unit speed in m/s |
| `fuel_percentage` | `float | None` | No | Current fuel fraction, where 1.0 is 100% |
| `life` | `float | int | None` | No | Current unit life value |
| `in_air` | `bool | None` | No | Whether the unit is currently in the air |
| `player_name` | `string | None` | No | Player name occupying this unit, if any |
| `loadout` | `object` | **Yes** | Loadout keyed by weapon type name |
| `tacan` | `MissionUnitNavAid` | **Yes** | TACAN status and channel data |
| `icls` | `MissionUnitNavAid` | **Yes** | ICLS status and channel data |
| `waypoints` | `list[MissionUnitWaypoint] | None` | No | Mission waypoints, if available |

#### Example

```json
{
  "current_location": {
    "alt": 58.6,
    "lat": 13.58402,
    "lon": 144.93082
  },
  "fuel_percentage": 0.85,
  "group_name": "Andersen AFB Group",
  "icls": {
    "active": false
  },
  "in_air": false,
  "life": 100.0,
  "loadout": {
    "AIM-120C": {
      "count": 2,
      "displayName": "AIM-120C AMRAAM"
    }
  },
  "player_name": "PilotNick",
  "speed": 0.0,
  "tacan": {
    "active": true,
    "channel": 67,
    "modeChannel": "X"
  },
  "type": "FA-18C_hornet",
  "unit_name": "Andersen AFB_F/A-18C Lot 20_0-1",
  "waypoints": [
    {
      "alt": 3000.0,
      "lat": 13.6,
      "lng": 145.0,
      "speed": 180.0
    }
  ]
}
```

### `MissionUnitWaypoint`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `lat` | `float` | **Yes** | Waypoint latitude in decimal degrees |
| `lng` | `float` | **Yes** | Waypoint longitude in decimal degrees |
| `alt` | `float | None` | No | Waypoint altitude in meters |
| `speed` | `float | None` | No | Waypoint speed in m/s |

#### Example

```json
{
  "alt": 3000.0,
  "lat": 13.6,
  "lng": 145.0,
  "speed": 180.0
}
```

### `MissionUnpauseResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

#### Example

```json
{
  "message": "Mission on server 'DCS Server' resumed.",
  "status": "success"
}
```

### `MissionUploadResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

#### Example

```json
{
  "message": "Mission 'Training Mission.miz' uploaded to server 'DCS Server'.",
  "status": "success"
}
```

### `MissionsResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `missions` | `list[MissionEntry]` | **Yes** | List of available missions |
| `count` | `int` | **Yes** | Total count of missions |

#### Example

```json
{
  "count": 2,
  "missions": [
    {
      "installed": true,
      "name": "Training Mission",
      "path": "Training Mission.miz"
    },
    {
      "installed": false,
      "name": "Combat Scenario",
      "path": "scenarios/Combat Scenario.miz"
    }
  ]
}
```

### `ModuleStats`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `module` | `string` | **Yes** | Aircraft/module name |
| `kills` | `int | None` | No | Number of kills with this module |
| `deaths` | `int | None` | No | Number of deaths with this module |
| `kdr` | `string | None` | No | Kill/Death ratio with this module |
| `playtime` | `int | None` | No | Total playtime with this module in seconds |

#### Example

```json
{
  "deaths": 10,
  "kdr": 3.0,
  "kills": 30,
  "module": "F/A-18C",
  "playtime": 3600
}
```

### `PlayerEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player name |
| `side` | `string` | **Yes** | Player side |
| `unit_type` | `string` | **Yes** | Type of aircraft |
| `callsign` | `string` | **Yes** | Callsign of the aircraft |
| `radios` | `list[int]` | **Yes** | List of radios |

#### Example

```json
{
  "callsign": "Chevy 1-1",
  "nick": "Pilot1",
  "radios": [
    127500000,
    251000000
  ],
  "side": "blue",
  "unit_type": "FA-18C_hornet"
}
```

### `PlayerInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `current_server` | `string | None` | No | Current server |
| `overall` | `PlayerStats` | **Yes** | Overall statistics |
| `last_session` | `PlayerStats` | **Yes** | Statistics of the last session |
| `module_stats` | `list[ModuleStats]` | No | Statistics by module |
| `credits` | `CampaignCredits | None` | No | Campaign credits of this player |
| `squadrons` | `list[PlayerSquadron]` | No | Squadrons the player is a member of |

#### Example

```json
{
  "credits": {
    "badge": "https://example.com/rookie_badge.png",
    "credits": 1500.0,
    "id": 1,
    "name": "Summer Campaign 2025",
    "rank": "Rookie"
  },
  "current_server": "DCS Server",
  "last_session": {
    "crashes": 0,
    "deaths": 2,
    "deaths_ground": 0,
    "deaths_helicopters": 0,
    "deaths_planes": 1,
    "deaths_pvp": 1,
    "deaths_sams": 1,
    "deaths_ships": 0,
    "ejections": 0,
    "kdr": 5.0,
    "kdrByModule": [
      {
        "kdr": 5.0,
        "module": "F/A-18C"
      }
    ],
    "kdr_pvp": 5.0,
    "kills": 10,
    "killsByModule": [
      {
        "kills": 10,
        "module": "F/A-18C"
      }
    ],
    "kills_ground": 3,
    "kills_helicopters": 1,
    "kills_planes": 4,
    "kills_pvp": 5,
    "kills_sams": 2,
    "kills_ships": 0,
    "landings": 3,
    "playtime": 3600,
    "takeoffs": 3,
    "teamkills": 0
  },
  "module_stats": [
    {
      "deaths": 10,
      "kdr": 3.0,
      "kills": 30,
      "module": "F/A-18C",
      "playtime": 3600
    }
  ],
  "overall": {
    "crashes": 22,
    "deaths": 30,
    "deaths_ground": 3,
    "deaths_helicopters": 3,
    "deaths_planes": 15,
    "deaths_pvp": 25,
    "deaths_sams": 8,
    "deaths_ships": 1,
    "ejections": 8,
    "kdr": 5.0,
    "kdrByModule": [
      {
        "kdr": 3.5,
        "module": "F/A-18C"
      }
    ],
    "kdr_pvp": 3.2,
    "kills": 150,
    "killsByModule": [
      {
        "kills": 50,
        "module": "F/A-18C"
      }
    ],
    "kills_ground": 50,
    "kills_helicopters": 15,
    "kills_planes": 60,
    "kills_pvp": 80,
    "kills_sams": 20,
    "kills_ships": 5,
    "landings": 270,
    "playtime": 7200,
    "takeoffs": 300,
    "teamkills": 1
  },
  "squadrons": [
    {
      "image_url": "https://example.com/squadron-logo.png",
      "name": "Red Devils"
    }
  ]
}
```

### `PlayerSquadron`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Squadron name |
| `image_url` | `string` | **Yes** | URL of the squadron's image |

#### Example

```json
{
  "image_url": "https://example.com/squadron-logo.png",
  "name": "Red Devils"
}
```

### `PlayerStats`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `playtime` | `int` | **Yes** | Total playtime in seconds |
| `kills` | `int` | **Yes** | Total kills |
| `deaths` | `int` | **Yes** | Total deaths |
| `kills_pvp` | `int` | **Yes** | Total PvP kills |
| `deaths_pvp` | `int` | **Yes** | Total PvP deaths |
| `kills_planes` | `int` | **Yes** | Total plane kills |
| `kills_helicopters` | `int` | **Yes** | Total helicopter kills |
| `kills_ships` | `int` | **Yes** | Total ship kills |
| `kills_sams` | `int` | **Yes** | Total SAM kills |
| `kills_ground` | `int` | **Yes** | Total ground kills |
| `deaths_planes` | `int` | **Yes** | Total plane deaths |
| `deaths_helicopters` | `int` | **Yes** | Total helicopter deaths |
| `deaths_ships` | `int` | **Yes** | Total ship deaths |
| `deaths_sams` | `int` | **Yes** | Total SAM deaths |
| `deaths_ground` | `int` | **Yes** | Total ground deaths |
| `takeoffs` | `int` | **Yes** | Number of takeoffs |
| `landings` | `int` | **Yes** | Number of landings |
| `ejections` | `int` | **Yes** | Number of ejections |
| `crashes` | `int` | **Yes** | Number of crashes |
| `teamkills` | `int` | **Yes** | Number of team kills |
| `kdr` | `string` | **Yes** | Kill/death ratio |
| `kdr_pvp` | `string` | **Yes** | PvP Kill/death ratio |
| `killsByModule` | `list[ModuleStats]` | No | PvP-Kills breakdown by module |
| `kdrByModule` | `list[ModuleStats]` | No | PvP-KDR breakdown by module |

#### Example

```json
{
  "crashes": 15,
  "deaths": 20,
  "deaths_ground": 2,
  "deaths_helicopters": 2,
  "deaths_planes": 10,
  "deaths_pvp": 20,
  "deaths_sams": 5,
  "deaths_ships": 1,
  "ejections": 5,
  "kdr": 2.5,
  "kdrByModule": [
    {
      "kdr": 2.5,
      "module": "F/A-18C"
    }
  ],
  "kdr_pvp": 2.5,
  "kills": 100,
  "killsByModule": [
    {
      "kills": 30,
      "module": "F/A-18C"
    }
  ],
  "kills_ground": 30,
  "kills_helicopters": 10,
  "kills_planes": 40,
  "kills_pvp": 50,
  "kills_sams": 15,
  "kills_ships": 5,
  "landings": 180,
  "playtime": 3600,
  "takeoffs": 200,
  "teamkills": 2
}
```

### `PlaytimeEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player nickname |
| `date` | `datetime` | **Yes** | Last seen timestamp |
| `playtime` | `int` | **Yes** | Total playtime in seconds |

#### Example

```json
{
  "date": "2025-08-07T12:00:00",
  "nick": "Player1",
  "playtime": 3600
}
```

### `Position`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `y` | `float` | **Yes** | Y |
| `x` | `float` | **Yes** | X |
| `z` | `float` | **Yes** | Z |

#### Example

```json
{
  "x": 76048.95,
  "y": 250.0,
  "z": 111344.92
}
```

### `PressureInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `pressureHPA` | `float` | **Yes** | Pressure in hPa |
| `pressureMM` | `float` | **Yes** | Pressure in mmHg |
| `pressureIN` | `float` | **Yes** | Pressure in inHg |

#### Example

```json
{
  "pressureHPA": 1013.25,
  "pressureIN": 29.92,
  "pressureMM": 760.0
}
```

### `ServerAttendanceStats`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `current_players` | `int` | **Yes** | Current number of active players |
| `unique_players_24h` | `int` | **Yes** | Unique players in last 24 hours |
| `total_playtime_hours_24h` | `float` | **Yes** | Total playtime hours in last 24 hours |
| `discord_members_24h` | `int` | **Yes** | Discord members who played in last 24 hours |
| `unique_players_7d` | `int` | **Yes** | Unique players in last 7 days |
| `total_playtime_hours_7d` | `float` | **Yes** | Total playtime hours in last 7 days |
| `discord_members_7d` | `int` | **Yes** | Discord members who played in last 7 days |
| `unique_players_30d` | `int` | **Yes** | Unique players in last 30 days |
| `total_playtime_hours_30d` | `float` | **Yes** | Total playtime hours in last 30 days |
| `discord_members_30d` | `int` | **Yes** | Discord members who played in last 30 days |
| `daily_trend` | `list[object]` | No | Daily unique player counts for trend analysis |
| `top_theatres` | `list[TopTheatre]` | No | Top theatres by playtime |
| `top_missions` | `list[TopMission]` | No | Top missions by playtime |
| `top_modules` | `list[TopModule]` | No | Top modules by playtime and usage |
| `total_sorties` | `int | None` | No | Total sorties flown |
| `total_kills` | `int | None` | No | Total kills |
| `total_deaths` | `int | None` | No | Total deaths |
| `total_pvp_kills` | `int | None` | No | Total PvP kills |
| `total_pvp_deaths` | `int | None` | No | Total PvP deaths |

#### Example

```json
{
  "current_players": 8,
  "daily_trend": [
    {
      "date": "2025-12-24",
      "unique_players": 15
    },
    {
      "date": "2025-12-25",
      "unique_players": 18
    }
  ],
  "discord_members_24h": 12,
  "discord_members_30d": 65,
  "discord_members_7d": 28,
  "top_missions": [
    {
      "mission_name": "Training Map",
      "playtime_hours": 1200
    },
    {
      "mission_name": "Combat Mission",
      "playtime_hours": 800
    }
  ],
  "top_modules": [
    {
      "module": "F/A-18C",
      "playtime_hours": 800,
      "total_uses": 127,
      "unique_players": 45
    }
  ],
  "top_theatres": [
    {
      "playtime_hours": 2500,
      "theatre": "Caucasus"
    },
    {
      "playtime_hours": 347,
      "theatre": "Syria"
    }
  ],
  "total_deaths": 567,
  "total_kills": 892,
  "total_playtime_hours_24h": 45.5,
  "total_playtime_hours_30d": 720.8,
  "total_playtime_hours_7d": 180.2,
  "total_pvp_deaths": 189,
  "total_pvp_kills": 234,
  "total_sorties": 1245,
  "unique_players_24h": 15,
  "unique_players_30d": 85,
  "unique_players_7d": 35
}
```

### `ServerInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Name of the server |
| `description` | `string` | **Yes** | Description of the server |
| `status` | `string` | **Yes** | Server status |
| `address` | `string` | **Yes** | IP address and port |
| `password` | `string` | **Yes** | Server password |
| `restart_time` | `datetime | None` | No | Restart time |
| `max_players` | `int | None` | No | Maximum number of players |
| `require_pure_clients` | `bool` | **Yes** | Whether to require pure clients |
| `require_pure_models` | `bool` | **Yes** | Whether to require pure models |
| `require_pure_scripts` | `bool` | **Yes** | Whether to require pure scripts |
| `require_pure_textures` | `bool` | **Yes** | Whether to require pure textures |
| `mission` | `MissionInfo | None` | No | Mission info |
| `extensions` | `list[ExtensionInfo]` | No | Extensions |
| `players` | `list[PlayerEntry]` | No | Players |
| `weather` | `WeatherInfo | None` | No | Current weather information |

#### Example

```json
{
  "address": "127.0.0.1:10308",
  "description": "Public Dedicated Server",
  "extensions": [
    {
      "name": "SRS",
      "value": "127.0.0.1:5002",
      "version": "1.9.0.0"
    }
  ],
  "max_players": 32,
  "mission": {
    "blue_slots": 20,
    "blue_slots_used": 5,
    "date_time": "2025-08-07 12:00:00",
    "name": "Training Mission",
    "red_slots": 20,
    "red_slots_used": 3,
    "restart_time": 1691424000,
    "theatre": "Caucasus",
    "uptime": 3600
  },
  "name": "DCS Server",
  "password": "secret",
  "players": [
    {
      "callsign": "Chevy 1-1",
      "nick": "Pilot1",
      "radios": [
        127500000,
        251000000
      ],
      "side": "blue",
      "unit_type": "FA-18C_hornet"
    }
  ],
  "require_pure_clients": true,
  "require_pure_models": true,
  "require_pure_scripts": true,
  "require_pure_textures": true,
  "restart_time": "2025-08-07T12:00:00",
  "status": "running",
  "weather": {
    "clouds_base": 8000,
    "clouds_density": 4,
    "clouds_thickness": 1000,
    "dust_enabled": false,
    "fog_enabled": false,
    "precipitation": 0,
    "pressure": 760.0,
    "temperature": 15.5,
    "visibility": 9999,
    "wind_direction": 270,
    "wind_speed": 5.2
  }
}
```

### `ServerRestartResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

#### Example

```json
{
  "message": "Server 'DCS Server' restarted.",
  "status": "success"
}
```

### `ServerStartResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

#### Example

```json
{
  "message": "Server 'DCS Server' started.",
  "status": "success"
}
```

### `ServerStats`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `totalPlayers` | `int` | **Yes** | Totalplayers |
| `avgPlaytime` | `int` | **Yes** | Avgplaytime |
| `totalPlaytime` | `int` | **Yes** | Totalplaytime |
| `activePlayers` | `int` | **Yes** | Activeplayers |
| `totalSorties` | `int` | **Yes** | Totalsorties |
| `totalKills` | `int` | **Yes** | Totalkills |
| `totalDeaths` | `int` | **Yes** | Totaldeaths |
| `totalPvPKills` | `int` | **Yes** | Totalpvpkills |
| `totalPvPDeaths` | `int` | **Yes** | Totalpvpdeaths |
| `daily_players` | `list[DailyPlayers]` | **Yes** | Daily Players |

#### Example

```json
{
  "activePlayers": 50,
  "avgPlaytime": 120,
  "daily_players": [
    {
      "date": "2025-08-07T12:00:00",
      "player_count": 100
    }
  ],
  "totalDeaths": 50,
  "totalKills": 100,
  "totalPlayers": 100,
  "totalPlaytime": 3600,
  "totalPvPDeaths": 20,
  "totalPvPKills": 30,
  "totalSorties": 100
}
```

### `ServerStopResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

#### Example

```json
{
  "message": "Server 'DCS Server' stopped.",
  "status": "success"
}
```

### `SquadronCampaignCredit`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `campaign` | `string | None` | No | Campaign name |
| `credits` | `float | None` | No | Squadron's credits in the campaign |

#### Example

```json
{
  "campaign": "Summer Campaign 2025",
  "credits": 1500.0
}
```

### `SquadronInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Name of the squadron |
| `description` | `string` | **Yes** | Description of the squadron |
| `image_url` | `string` | **Yes** | URL to the squadron's image |
| `locked` | `bool` | **Yes** | Whether the squadron is locked |
| `role` | `string | None` | No | Discord role name associated with the squadron |
| `members` | `list[UserEntry]` | No | Members |

#### Example

```json
{
  "description": "Elite Fighter Squadron",
  "image_url": "https://example.com/squadron-logo.png",
  "locked": true,
  "members": [
    {
      "current_server": "My Fancy Server",
      "date": "2025-08-07T12:00:00",
      "discord_id": 123456789012345678,
      "nick": "Player1",
      "ucid": "aabbccddeeffgghhiiffkk1234567890"
    }
  ],
  "name": "Red Devils",
  "role": "Squadron Leader"
}
```

### `TopKill`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `row_num` | `int` | **Yes** | Row number |
| `nick` | `string` | **Yes** | Player's nickname |
| `date` | `datetime` | **Yes** | Last seen date of that player in ISO-format |
| `kills` | `int` | **Yes** | Number of kills |
| `deaths` | `int` | **Yes** | Number of deaths |
| `kdr` | `float` | **Yes** | Kill/Death ratio |
| `kills_pvp` | `int` | **Yes** | Number of kills in PvP |
| `deaths_pvp` | `int` | **Yes** | Number of deaths in PvP |
| `kdr_pvp` | `float` | **Yes** | Kill/Death ratio in PvP |
| `playtime` | `int` | **Yes** | Total playtime in seconds |
| `credits` | `int` | **Yes** | Total credits earned |

#### Example

```json
{
  "credits": 1500,
  "date": "2025-01-01T00:00:00",
  "deaths": 2,
  "deaths_pvp": 0,
  "kdr": 5.0,
  "kdr_pvp": 5.0,
  "kills": 10,
  "kills_pvp": 5,
  "nick": "Special K",
  "playtime": 7200,
  "row_num": 1
}
```

### `TopMission`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `mission_name` | `string` | **Yes** | Mission Name |
| `playtime_hours` | `int` | **Yes** | Playtime Hours |

#### Example

```json
{
  "mission_name": "Training Map",
  "playtime_hours": 1200
}
```

### `TopModule`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `module` | `string` | **Yes** | Module |
| `playtime_hours` | `int` | **Yes** | Playtime Hours |
| `unique_players` | `int` | **Yes** | Unique Players |
| `total_uses` | `int` | **Yes** | Total Uses |

#### Example

```json
{
  "module": "F/A-18C",
  "playtime_hours": 800,
  "total_uses": 127,
  "unique_players": 45
}
```

### `TopTheatre`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `theatre` | `string` | **Yes** | Theatre |
| `playtime_hours` | `int` | **Yes** | Playtime Hours |

#### Example

```json
{
  "playtime_hours": 2500,
  "theatre": "Caucasus"
}
```

### `TrapEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `int` | **Yes** | Trap ID |
| `unit_type` | `string` | **Yes** | Type of aircraft |
| `grade` | `string` | **Yes** | Landing grade |
| `comment` | `string` | **Yes** | Landing comment |
| `place` | `string` | **Yes** | Landing location |
| `trapcase` | `int` | **Yes** | Trap case number |
| `wire` | `int | None` | **Yes** | Arresting wire number |
| `night` | `bool` | **Yes** | Whether landing was at night |
| `points` | `int` | **Yes** | Points awarded for the trap |
| `time` | `datetime` | **Yes** | Time of the trap |

#### Example

```json
{
  "comment": "Good pass",
  "grade": "OK",
  "id": 1,
  "night": false,
  "place": "CVN-73",
  "points": 100,
  "time": "2025-08-07T12:00:00",
  "trapcase": 3,
  "unit_type": "F/A-18C",
  "wire": 3
}
```

### `Trueskill`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player's nickname |
| `date` | `datetime` | **Yes** | Last seen date of that player in ISO-format |
| `kills_pvp` | `int` | **Yes** | Number of PvP kills |
| `deaths_pvp` | `int` | **Yes** | Number of deaths by other players |
| `TrueSkill` | `float` | **Yes** | TrueSkill:tm: Rating of that player |

#### Example

```json
{
  "TrueSkill": 18.6,
  "date": "2025-01-01T00:00:00",
  "deaths_pvp": 2,
  "kills_pvp": 10,
  "nick": "Special K"
}
```

### `UserEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `ucid` | `string` | **Yes** | DCS account ID |
| `discord_id` | `int` | **Yes** | Discord user ID |
| `nick` | `string` | **Yes** | Player nickname |
| `date` | `datetime` | **Yes** | Last seen timestamp |
| `current_server` | `string | None` | No | Current server |

#### Example

```json
{
  "current_server": "My Fancy Server",
  "date": "2025-08-07T12:00:00",
  "discord_id": 123456789012345678,
  "nick": "Player1",
  "ucid": "aabbccddeeffgghhiiffkk1234567890"
}
```

### `WeaponPK`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `weapon` | `string` | **Yes** | Weapon type |
| `shots` | `int` | **Yes** | Number of shots fired |
| `hits` | `int` | **Yes** | Number of hits |
| `pk` | `string` | **Yes** | Probability of killing |

#### Example

```json
{
  "hits": 10,
  "pk": 0.5,
  "shots": 20,
  "weapon": "AIM-120C"
}
```

### `WeatherInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `temperature` | `float | None` | No | Temperature in Celsius |
| `wind_speed` | `float | None` | No | Wind speed in kts |
| `wind_direction` | `int | None` | No | Wind direction in degrees |
| `turbulence` | `float | None` | No | Turbulence at ground in kts |
| `pressure` | `float | None` | No | Atmospheric pressure in mmHg |
| `clouds_base` | `int | None` | No | Cloud base altitude in feet |
| `clouds_density` | `int | None` | No | Cloud density (0-10) |
| `clouds_thickness` | `int | None` | No | Cloud thickness in feet |
| `precipitation` | `int | None` | No | Precipitation type (0=none, 1=rain, 2=thunderstorm, 3=snow) |
| `fog_enabled` | `bool | None` | No | Fog enabled |
| `dust_enabled` | `bool | None` | No | Dust storm enabled |
| `visibility` | `int | None` | No | Visibility in meters |

#### Example

```json
{
  "clouds_base": 8000,
  "clouds_density": 4,
  "clouds_thickness": 1000,
  "dust_enabled": false,
  "fog_enabled": false,
  "precipitation": 0,
  "pressure": 760.0,
  "temperature": 15.5,
  "visibility": 9999,
  "wind_direction": 270,
  "wind_speed": 5.2
}
```

### `WindInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `speed` | `float` | **Yes** | Wind speed in m/s |
| `dir` | `float` | **Yes** | Wind direction in degrees |

#### Example

```json
{
  "dir": 270.0,
  "speed": 5.2
}
```
