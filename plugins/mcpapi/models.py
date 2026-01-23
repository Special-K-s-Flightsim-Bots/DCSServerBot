from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal


class PluginInfo(BaseModel):
    """Information about a loaded plugin."""
    name: str = Field(..., description="Plugin name")
    version: str = Field(..., description="Plugin version")

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "mission",
                "version": "3.2"
            }
        }
    }


class BotStatus(BaseModel):
    """Bot status information."""
    running: bool = Field(..., description="Whether the bot is running")
    uptime_seconds: float = Field(..., description="Bot uptime in seconds")
    version: str = Field(..., description="Bot version string")
    sub_version: str = Field(..., description="Bot sub-version string")
    node_name: str = Field(..., description="Name of this node")
    is_master: bool = Field(..., description="Whether this node is the master")
    plugins: list[PluginInfo] = Field(..., description="List of loaded plugins")

    model_config = {
        "json_schema_extra": {
            "example": {
                "running": True,
                "uptime_seconds": 3600.5,
                "version": "3.5",
                "sub_version": "20260123",
                "node_name": "server",
                "is_master": True,
                "plugins": [
                    {"name": "mission", "version": "3.2"},
                    {"name": "admin", "version": "2.1"}
                ]
            }
        }
    }


class HealthCheck(BaseModel):
    """Health check response."""
    status: Literal["healthy", "degraded", "unhealthy"] = Field(..., description="Overall health status")
    database: Literal["connected", "disconnected"] = Field(..., description="Database connection status")
    servers_online: int = Field(..., description="Number of DCS servers online")
    servers_total: int = Field(..., description="Total number of configured DCS servers")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "healthy",
                "database": "connected",
                "servers_online": 2,
                "servers_total": 3
            }
        }
    }


class LogEntry(BaseModel):
    """A log entry."""
    timestamp: str = Field(..., description="Log timestamp")
    level: str = Field(..., description="Log level (DEBUG, INFO, WARNING, ERROR)")
    message: str = Field(..., description="Log message")


class LogsResponse(BaseModel):
    """Response containing log entries."""
    entries: list[LogEntry] = Field(..., description="Log entries")
    total_lines: int = Field(..., description="Total number of lines in the log file")


class PlayerInfo(BaseModel):
    """Information about a connected player."""
    id: int = Field(..., description="Player slot ID")
    name: str = Field(..., description="Player name")
    ucid: str = Field(..., description="Player UCID")
    side: str = Field(..., description="Coalition (blue/red/spectator)")
    slot: str | None = Field(None, description="Current aircraft slot")
    unit_type: str | None = Field(None, description="Aircraft type if in a slot")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": 1,
                "name": "Maverick",
                "ucid": "abc123def456",
                "side": "blue",
                "slot": "F-14B_1",
                "unit_type": "F-14B"
            }
        }
    }


class MissionInfo(BaseModel):
    """Information about the current mission."""
    name: str = Field(..., description="Mission name")
    filename: str = Field(..., description="Mission filename")
    theatre: str = Field(..., description="Map/theatre name")
    start_time: str = Field(..., description="Mission start time (in-game)")
    real_time: int = Field(..., description="Real time elapsed in seconds")
    pause: bool = Field(..., description="Whether the mission is paused")

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Operation Thunder",
                "filename": "operation_thunder.miz",
                "theatre": "Caucasus",
                "start_time": "08:00:00",
                "real_time": 3600,
                "pause": False
            }
        }
    }


class ServerStatus(BaseModel):
    """DCS server status information."""
    name: str = Field(..., description="Server name")
    status: str = Field(..., description="Server status (running, paused, stopped, etc.)")
    num_players: int = Field(..., description="Number of connected players")
    max_players: int = Field(..., description="Maximum player slots")
    mission: MissionInfo | None = Field(None, description="Current mission info if running")
    players: list[PlayerInfo] = Field(default_factory=list, description="Connected players")

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "My DCS Server",
                "status": "running",
                "num_players": 12,
                "max_players": 64,
                "mission": {
                    "name": "Operation Thunder",
                    "filename": "operation_thunder.miz",
                    "theatre": "Caucasus",
                    "start_time": "08:00:00",
                    "real_time": 3600,
                    "pause": False
                },
                "players": []
            }
        }
    }


class ChatMessage(BaseModel):
    """Chat message to send."""
    message: str = Field(..., description="Message text to send")
    sender: str = Field(default="Server", description="Sender name to display")


class ChatResponse(BaseModel):
    """Response after sending a chat message."""
    success: bool = Field(..., description="Whether the message was sent")
    message: str = Field(..., description="Status message")


class LogisticsTaskCreate(BaseModel):
    """Request to create a logistics task."""
    source: str = Field(..., description="Pickup location name (airbase/FARP)")
    destination: str = Field(..., description="Delivery location name")
    cargo: str = Field(..., description="Description of cargo to deliver")
    priority: Literal["low", "normal", "high", "urgent"] = Field(default="normal", description="Task priority")
    coalition: Literal["red", "blue"] = Field(default="blue", description="Which coalition can accept the task")

    model_config = {
        "json_schema_extra": {
            "example": {
                "source": "Herat",
                "destination": "Shindand",
                "cargo": "Medical Supplies",
                "priority": "normal",
                "coalition": "blue"
            }
        }
    }


class LogisticsTaskResponse(BaseModel):
    """Response after creating a logistics task."""
    success: bool = Field(..., description="Whether the task was created")
    task_id: int | None = Field(None, description="The created task ID")
    message: str = Field(..., description="Status message")
    discord_posted: bool = Field(default=False, description="Whether the task was posted to Discord")


class CommandParameter(BaseModel):
    """Information about a command parameter."""
    name: str = Field(..., description="Parameter name")
    type: str = Field(..., description="Parameter type")
    required: bool = Field(..., description="Whether the parameter is required")
    description: str | None = Field(None, description="Parameter description")
    default: str | None = Field(None, description="Default value if any")


class CommandInfo(BaseModel):
    """Information about a slash command."""
    name: str = Field(..., description="Full command name (e.g., 'logistics create')")
    description: str = Field(..., description="Command description")
    parameters: list[CommandParameter] = Field(default_factory=list, description="Command parameters")


class CommandListResponse(BaseModel):
    """Response containing available commands."""
    commands: list[CommandInfo] = Field(..., description="List of available commands")


class SlashCommandRequest(BaseModel):
    """Request to execute a slash command."""
    command: str = Field(..., description="Full command path (e.g., 'logistics list' or 'mission restart')")
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Command parameters as key-value pairs")

    model_config = {
        "json_schema_extra": {
            "example": {
                "command": "logistics list",
                "parameters": {
                    "server": "DCS Server",
                    "status": "approved"
                }
            }
        }
    }


class EmbedField(BaseModel):
    """A field in a Discord embed."""
    name: str
    value: str
    inline: bool = False


class EmbedInfo(BaseModel):
    """Discord embed information."""
    title: str | None = None
    description: str | None = None
    color: int | None = None
    fields: list[EmbedField] = Field(default_factory=list)
    footer: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None


class SlashCommandResponse(BaseModel):
    """Response from executing a slash command."""
    success: bool = Field(..., description="Whether the command executed successfully")
    content: str | None = Field(None, description="Text content of the response")
    embeds: list[EmbedInfo] = Field(default_factory=list, description="Embed responses")
    error: str | None = Field(None, description="Error message if failed")
