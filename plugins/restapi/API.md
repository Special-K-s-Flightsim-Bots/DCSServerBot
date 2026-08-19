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

---

### `GET` /airbase/atis

**Summary:** Airbase ATIS

**Description:** Get ATIS information for an airbase on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |
| `airbase_name` | `string` | query | **Yes** | - | Airbase Name |

**Response:** `any`

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

---

### `GET` /server_attendance

**Summary:** Server Attendance Statistics

**Description:** Get detailed server attendance statistics

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | No | - | Server Name |

**Response:** `ServerAttendanceStats`

---

### `GET` /servers

**Summary:** Server list

**Description:** List all servers, the active mission (if any) and the active extensions

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string | None` | query | No | - | Server Name |

**Response:** `list[ServerInfo]`

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

---

### `GET` /traps/img

**Summary:** Carrier Trap Image

**Description:** Get trap image for a player

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `trap_id` | `int` | query | **Yes** | - | Trap Id |

**Response:** `string`

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

---

### `GET` /mission/bullseyes

**Summary:** Mission Bullseyes

**Description:** Get the bullseye coordinates for blue and red coalitions in the current mission.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Name of the server |

**Response:** `MissionBullseyesResponse`

---

### `GET` /mission/drawings

**Summary:** Mission Drawings

**Description:** Get mission drawing objects grouped by drawing layer.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Name of the server |

**Response:** `MissionDrawingsResponse`

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

---

### `POST` /instance/stop

**Summary:** Stop a server instance.

**Description:** Stop a server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `ServerStopResponse`

---

### `POST` /instance/restart

**Summary:** Restart a server instance.

**Description:** Restart a server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `ServerRestartResponse`

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

---

### `POST` /instance/mission/pause

**Summary:** Pause mission for a server instance.

**Description:** Pause the mission on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `MissionPauseResponse`

---

### `POST` /instance/mission/unpause

**Summary:** Unpause mission for a server instance.

**Description:** Unpause the mission on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `MissionUnpauseResponse`

---

### `POST` /instance/mission/restart

**Summary:** Restart mission for a server instance.

**Description:** Restart the mission on a given server.

#### Parameters

| Name | Type | In | Required | Default | Description |
|------|------|----|----------|---------|-------------|
| `server_name` | `string` | query | **Yes** | - | Server Name |

**Response:** `MissionRestartResponse`

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

### `AirbaseCaptureResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `server_name` | `string` | **Yes** | Server name |
| `airbase_name` | `string` | **Yes** | Airbase name |
| `coalition` | `int` | **Yes** | Coalition capturing the airbase |

### `AirbaseInfoResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `airbase` | `object` | **Yes** | Airbase data |

### `AirbaseSetWarehouseItemResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `item` | `string` | **Yes** | Warehouse item name |
| `server_name` | `string` | **Yes** | Server name |
| `value` | `int` | **Yes** | Quantity value |

### `AirbaseWarehouseResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `warehouse` | `object` | **Yes** | Warehouse data |
| `unlimited` | `object` | **Yes** | Unlimited flags |

### `AirbasesResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `airbases` | `list[Airbase]` | **Yes** | Airbases data |

### `CampaignCredits`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | `int` | **Yes** | Campaign ID |
| `name` | `string` | **Yes** | Campaign name |
| `credits` | `float` | **Yes** | Player's credits in this campaign |
| `rank` | `string | None` | **Yes** | Player's rank |
| `badge` | `string | None` | **Yes** | Player's badge |

### `ConvertCoordinates`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `latlon` | `string` | **Yes** | Latitude and Longitude in decimal degrees |
| `mgrs` | `string` | **Yes** | Cooridnate provided, converted to MGRS |
| `dms` | `string` | **Yes** | Cooridnate provided, converted to Decimal, Minutes, Seconds |
| `ddm` | `string` | **Yes** | Cooridnate provided, converted to Degrees and Decimal Minutes |
| `meters` | `object` | **Yes** | Cooridnate provided, converted to DCS Meters |

### `DailyPlayers`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `date` | `datetime` | **Yes** | Date |
| `player_count` | `int` | **Yes** | Player Count |

### `Dynamic`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `dynamicSpawnAvailable` | `bool` | **Yes** | Dynamicspawnavailable |
| `allowHotSpawn` | `bool` | **Yes** | Allowhotspawn |

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

### `ExtensionInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Name |
| `version` | `string | None` | No | Version |
| `value` | `string` | **Yes** | Value |

### `GreenieboardEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player name |
| `traps` | `list[TrapEntry]` | **Yes** | List of traps for this player |

### `GreenieboardResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `players` | `list[GreenieboardEntry]` | **Yes** | All players and their traps |

### `GroupWaypointsResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `group_name` | `string` | **Yes** | Name of the group |
| `group_type` | `string` | **Yes** | Type of the group |
| `waypoints` | `object` | **Yes** | Keyed waypoint dictionary (wp1, wp2, ...) with lat/lon per waypoint |

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

### `HighscoreEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player nickname |
| `date` | `datetime` | **Yes** | Last seen timestamp |
| `value` | `string` | **Yes** | Score value (varies by category) |

### `LeaderBoard`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `items` | `list[TopKill]` | **Yes** | Items |
| `total_count` | `int` | **Yes** | Total Count |
| `offset` | `int` | **Yes** | Offset |

### `LinkMeResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `token` | `string | None` | No | 4-digit token for linking DCS and Discord accounts |
| `timestamp` | `string | None` | No | Expiry timestamp in ISO format |
| `rc` | `int` | **Yes** | Return code bitmask (1=User linked, 2=Link in progress, 4=Force operation) |

### `MissionBullseye`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `coalition` | `string` | **Yes** | Coalition name ('blue' or 'red') |
| `lat` | `float` | **Yes** | Bullseye latitude in decimal degrees |
| `lng` | `float` | **Yes** | Bullseye longitude in decimal degrees |

### `MissionBullseyesResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `bullseyes` | `list[MissionBullseye]` | **Yes** | List of coalition bullseye coordinates |

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

### `MissionDrawingPoint`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `lat` | `float` | **Yes** | Latitude in decimal degrees |
| `lng` | `float` | **Yes** | Longitude in decimal degrees |

### `MissionDrawingsResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `drawings` | `object` | **Yes** | Drawings keyed by layer name; each drawing contains primitive-specific fields |

### `MissionEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Mission name without extension |
| `path` | `string` | **Yes** | Relative path to mission file |
| `installed` | `bool` | **Yes** | Whether mission is in the active mission list |

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

### `MissionLoadResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

### `MissionPauseResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

### `MissionRestartResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

### `MissionUnitLoadoutItem`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `displayName` | `string` | **Yes** | Display name of the weapon or store |
| `count` | `int` | **Yes** | Remaining count |

### `MissionUnitLocation`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `lat` | `float` | **Yes** | Current latitude in decimal degrees |
| `lon` | `float` | **Yes** | Current longitude in decimal degrees |
| `alt` | `float` | **Yes** | Current altitude in meters |

### `MissionUnitNavAid`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `active` | `bool` | **Yes** | Whether this navaid is active |
| `channel` | `int | None` | No | Configured channel if available |
| `modeChannel` | `string | int | None` | No | TACAN mode channel, if available |

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

### `MissionUnitWaypoint`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `lat` | `float` | **Yes** | Waypoint latitude in decimal degrees |
| `lng` | `float` | **Yes** | Waypoint longitude in decimal degrees |
| `alt` | `float | None` | No | Waypoint altitude in meters |
| `speed` | `float | None` | No | Waypoint speed in m/s |

### `MissionUnpauseResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

### `MissionUploadResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

### `MissionsResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `missions` | `list[MissionEntry]` | **Yes** | List of available missions |
| `count` | `int` | **Yes** | Total count of missions |

### `ModuleStats`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `module` | `string` | **Yes** | Aircraft/module name |
| `kills` | `int | None` | No | Number of kills with this module |
| `deaths` | `int | None` | No | Number of deaths with this module |
| `kdr` | `string | None` | No | Kill/Death ratio with this module |
| `playtime` | `int | None` | No | Total playtime with this module in seconds |

### `PlayerEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player name |
| `side` | `string` | **Yes** | Player side |
| `unit_type` | `string` | **Yes** | Type of aircraft |
| `callsign` | `string` | **Yes** | Callsign of the aircraft |
| `radios` | `list[int]` | **Yes** | List of radios |

### `PlayerInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `current_server` | `string | None` | No | Current server |
| `overall` | `PlayerStats` | **Yes** | Overall statistics |
| `last_session` | `PlayerStats` | **Yes** | Statistics of the last session |
| `module_stats` | `list[ModuleStats]` | No | Statistics by module |
| `credits` | `CampaignCredits | None` | No | Campaign credits of this player |
| `squadrons` | `list[PlayerSquadron]` | No | Squadrons the player is a member of |

### `PlayerSquadron`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Squadron name |
| `image_url` | `string` | **Yes** | URL of the squadron's image |

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

### `PlaytimeEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player nickname |
| `date` | `datetime` | **Yes** | Last seen timestamp |
| `playtime` | `int` | **Yes** | Total playtime in seconds |

### `Position`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `y` | `float` | **Yes** | Y |
| `x` | `float` | **Yes** | X |
| `z` | `float` | **Yes** | Z |

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

### `ServerRestartResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

### `ServerStartResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

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

### `ServerStopResponse`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | `string` | **Yes** | Status of the operation |
| `message` | `string` | **Yes** | Status message |

### `SquadronCampaignCredit`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `campaign` | `string | None` | No | Campaign name |
| `credits` | `float | None` | No | Squadron's credits in the campaign |

### `SquadronInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | `string` | **Yes** | Name of the squadron |
| `description` | `string` | **Yes** | Description of the squadron |
| `image_url` | `string` | **Yes** | URL to the squadron's image |
| `locked` | `bool` | **Yes** | Whether the squadron is locked |
| `role` | `string | None` | No | Discord role name associated with the squadron |
| `members` | `list[UserEntry]` | No | Members |

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

### `TopMission`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `mission_name` | `string` | **Yes** | Mission Name |
| `playtime_hours` | `int` | **Yes** | Playtime Hours |

### `TopModule`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `module` | `string` | **Yes** | Module |
| `playtime_hours` | `int` | **Yes** | Playtime Hours |
| `unique_players` | `int` | **Yes** | Unique Players |
| `total_uses` | `int` | **Yes** | Total Uses |

### `TopTheatre`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `theatre` | `string` | **Yes** | Theatre |
| `playtime_hours` | `int` | **Yes** | Playtime Hours |

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

### `Trueskill`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `nick` | `string` | **Yes** | Player's nickname |
| `date` | `datetime` | **Yes** | Last seen date of that player in ISO-format |
| `kills_pvp` | `int` | **Yes** | Number of PvP kills |
| `deaths_pvp` | `int` | **Yes** | Number of deaths by other players |
| `TrueSkill` | `float` | **Yes** | TrueSkill:tm: Rating of that player |

### `UserEntry`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `ucid` | `string` | **Yes** | DCS account ID |
| `discord_id` | `int` | **Yes** | Discord user ID |
| `nick` | `string` | **Yes** | Player nickname |
| `date` | `datetime` | **Yes** | Last seen timestamp |
| `current_server` | `string | None` | No | Current server |

### `WeaponPK`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `weapon` | `string` | **Yes** | Weapon type |
| `shots` | `int` | **Yes** | Number of shots fired |
| `hits` | `int` | **Yes** | Number of hits |
| `pk` | `string` | **Yes** | Probability of killing |

### `WeatherInfo`

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `temperature` | `float | None` | No | Temperature in Celsius |
| `wind_speed` | `float | None` | No | Wind speed in m/s |
| `wind_direction` | `int | None` | No | Wind direction in degrees |
| `pressure` | `float | None` | No | Atmospheric pressure in mmHg |
| `visibility` | `int | None` | No | Visibility in meters |
| `clouds_base` | `int | None` | No | Cloud base altitude in feet |
| `clouds_density` | `int | None` | No | Cloud density (0-10) |
| `precipitation` | `int | None` | No | Precipitation type (0=none, 1=rain, 2=thunderstorm, 3=snow) |
| `fog_enabled` | `bool | None` | No | Fog enabled |
| `fog_visibility` | `int | None` | No | Fog visibility in meters |
| `dust_enabled` | `bool | None` | No | Dust storm enabled |
| `dust_visibility` | `int | None` | No | Dust storm visibility in meters |
