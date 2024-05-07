import os
import tempfile
import asyncio
from decimal import Decimal
import gzip
import base64
import struct
import json
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands
from core import Plugin, utils, Server, command, Status
from services import DCSServerBot


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


def ddm_to_dmm(coordinate):
    degrees = int(coordinate)
    decimal_minutes = (coordinate - degrees) * 60
    return degrees, decimal_minutes


def format_latitude(degrees, decimal_minutes):
    whole_minutes = int(decimal_minutes)
    fractional_minutes = (decimal_minutes - whole_minutes) * 60
    formatted_minutes = '{:02d}.{:03d}'.format(whole_minutes, int(fractional_minutes * 1000))
    return f"N {degrees:02d}\u00b0{formatted_minutes[:6]}\u2019"  # Limit decimal places to three


def format_longitude(degrees, decimal_minutes):
    whole_minutes = int(decimal_minutes)
    fractional_minutes = (decimal_minutes - whole_minutes) * 60
    formatted_minutes = '{:02d}.{:03d}'.format(whole_minutes, int(fractional_minutes * 1000))
    return f"E {degrees:03d}\u00b0{formatted_minutes[:6]}\u2019"  # Limit decimal places to three


# Converts the coordinates to DMM and formats it as a json based on airframe
def convert_coordinates(coordinates, airframe):
    converted_coordinates = []
    for i, coordinate in enumerate(coordinates, 1):
        latitude = Decimal(coordinate[0])
        longitude = Decimal(coordinate[1])
        elevation = round(float(coordinate[2]) * 3.28084)  # Convert meters to feet and round
        latitude_degrees, latitude_decimal_minutes = ddm_to_dmm(latitude)
        longitude_degrees, longitude_decimal_minutes = ddm_to_dmm(longitude)
        formatted_latitude = format_latitude(latitude_degrees, latitude_decimal_minutes)
        formatted_longitude = format_longitude(longitude_degrees, longitude_decimal_minutes)
        if airframe == "15" or airframe == "intel":
            converted_coordinates.append({
                "Sequence": i,
                "Name": f"WPT {i}",
                "Latitude": formatted_latitude,
                "Longitude": formatted_longitude,
                "Elevation": elevation,
                "Target": True,
                "IsCoordinateBlank": False
            })
        elif airframe == "16":
            converted_coordinates.append({
                "Sequence": i,
                "Name": f"WPT {i}",
                "Latitude": formatted_latitude,
                "Longitude": formatted_longitude,
                "Elevation": elevation,
                "TimeOverSteerpoint": "00:00:00",
                "UseOA": False,
                "OffsetAimpoint1": None,
                "OffsetAimpoint2": None,
                "UseVIP": False,
                "VIPtoTGT": None,
                "VIPtoPUP": None,
                "UseVRP": False,
                "TGTtoVRP": None,
                "TGTtoPUP": None,
                "IsCoordinateBlank": False
            })
        elif airframe == "18":
            converted_coordinates.append({
                "Sequence": i,
                "Name": f"WPT {i}",
                "Latitude": formatted_latitude,
                "Longitude": formatted_longitude,
                "Elevation": elevation,
                "Blank": False
            })
    return converted_coordinates


class Dtc(Plugin):

    @command(description='Send waypoint information in the channel.')
    @utils.app_has_role('DCS')
    @commands.guild_only()
    async def intel(self, interaction: discord.Interaction,
                    server: app_commands.Transform[
                        Server, utils.ServerTransformer(status=[Status.PAUSED, Status.RUNNING])],
                    color: Literal['red', 'blue'], waypoint_range: str):

        try:
            # Convert the waypoint_range argument to a range of integers
            try:
                start, end = map(int, waypoint_range.split(','))
                if start > end:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message('Invalid waypoint range. The start index should be less '
                                                            'than or equal to the end index.', ephemeral=True)
                    return
                waypoint_indices = list(range(start - 1, end))  # Subtract 1 to adjust for 0-based indexing
            except ValueError:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message('Invalid waypoint range. Please specify as a pair of '
                                                        'comma-separated integers.', ephemeral=True)
                return

            dtc_ready_data = await server.send_to_dcs_sync({
                "command": "getVariable",
                "name": "dtcReady"
            })

            dtc_ready = dtc_ready_data.get('value', 0)

            # Extract status for a specific frontline
            frontline_status_data = await server.send_to_dcs_sync({
                "command": "getVariable",
                "name": "FrontlineStatus"
            })

            frontline_status = frontline_status_data.get('value', '')

            status_dict = dict(item.split(': ') for item in frontline_status.split('\n'))
            frontline_number = int(status_dict.get(color.title(), "0"))

            # File path to be used by the plugin
            file_path = f"plugins/dtc/tmp/"

            # Check if dtcReady is 1.
            if int(dtc_ready) == 1:
                # Get the values of blueLL and redLL.
                blueLL_data = await server.send_to_dcs_sync({
                    "command": "getVariable",
                    "name": "blueLL"
                })

                redLL_data = await server.send_to_dcs_sync({
                    "command": "getVariable",
                    "name": "redLL"
                })

                # Reverse the order of dictionaries in blueLL_data
                blueLL_data_reversed = blueLL_data.get('value', [])[::-1]

                # Save blueLL values into a text file.
                with open(f'{file_path}{server.name}-blueLL_data.txt', 'w') as f:
                    json.dump({"blueLL": blueLL_data_reversed}, f)

                # Reverse the order of dictionaries in redLL_data
                redLL_data_reversed = redLL_data.get('value', [])[::-1]

                # Save redLL values into a text file.
                with open(f'{file_path}{server.name}-redLL_data.txt', 'w') as f:
                    json.dump({"redLL": redLL_data_reversed}, f)

                # Set dtcReady to 0.
                server.send_to_dcs({
                    "command": "setVariable",
                    "name": "dtcReady",
                    "value": 0
                })

            # Extract the list of coordinates from the jjson file
            with open(f"{file_path}{server.name}-{color}LL_data.txt", 'r') as f:
                data = json.load(f)
            coordinates = data.get(f"{color.lower()}LL", [])

            # Check if the coordinates data is a list
            if not isinstance(coordinates, list):
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(f"No {color}LL data available.", ephemeral=True)
                return

            # Select only the waypoints specified by the user
            selected_waypoints = [coordinates[index] for index in waypoint_indices if index < len(coordinates)]

            # Convert the selected waypoints to the required format
            converted_list = convert_coordinates(
                [(waypoint['lat'], waypoint['long'], waypoint['alt']) for waypoint in selected_waypoints], "intel")

            # Create header rows.
            header1 = f"TGT information for {color.title()}'s frontline #{frontline_number}:"
            header2 = f"{'TGT':<3} {'Lat':<15} {'Lon':<15} {'Elev (ft)':<7}"
            underline_header2 = f"{'---':<3} {'---':<15} {'---':<15} {'---':<7}"

            # Create a list of strings, each representing a row in the table.
            rows = [f"{i + 1:<3} {wpt['Latitude']:<15} {wpt['Longitude']:<15} {wpt['Elevation']:<7}"
                    for i, wpt in enumerate(converted_list)]

            # Add the headers to the beginning of the rows list.
            rows.insert(0, underline_header2)
            rows.insert(0, header2)
            rows.insert(0, header1)

            # Combine the rows into a single string, with each row on a new line.
            waypoint_info = "\n".join(rows)

            # Surround the string with triple backticks to create a code block.
            waypoint_info = f"```\n{waypoint_info}\n```"

            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(waypoint_info)

        except Exception as e:
            # Print the error message
            self.log.exception(e)
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("An error occurred while processing your command.", ephemeral=True)

    @command(description='Sends a json file to the user.')
    @utils.app_has_role('DCS')
    @commands.guild_only()
    async def dtc(self, interaction: discord.Interaction,
                  server: app_commands.Transform[
                      Server, utils.ServerTransformer(status=[Status.PAUSED, Status.RUNNING])],
                  airframe: Literal['15', '16', '18'], color: Literal['red', 'blue'], waypoint_range: str):
        # Convert the waypoint_range argument to a range of integers
        try:
            start, end = map(int, waypoint_range.split(','))
            if start > end:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_messsage('Invalid waypoint range. The start index should be less than '
                                                         'or equal to the end index.', ephemeral=True)
                return
            waypoint_indices = list(range(start - 1, end))  # Subtract 1 to adjust for 0-based indexing
        except ValueError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message('Invalid waypoint range. Please specify as a pair of '
                                                    'comma-separated integers.', ephemeral=True)
            return

        dtc_ready_data = await server.send_to_dcs_sync({
            "command": "getVariable",
            "name": "dtcReady"
        })

        dtc_ready = dtc_ready_data.get('value', 0)
        file_path = "plugins/dtc/tmp/"

        # Check if dtcReady is 1.
        if dtc_ready == 1 or dtc_ready == '1':
            # Get the values of blueLL and redLL.
            blueLL_data = await server.send_to_dcs_sync({
                "command": "getVariable",
                "name": "blueLL"
            })

            redLL_data = await server.send_to_dcs_sync({
                "command": "getVariable",
                "name": "redLL"
            })

            # Reverse the order of dictionaries in blueLL_data
            blueLL_data_reversed = blueLL_data.get('value', [])[::-1]

            # Save blueLL values into a text file.
            with open(f'{file_path}{server.name}-blueLL_data.txt', 'w') as f:
                json.dump({"blueLL": blueLL_data_reversed}, f)

            # Reverse the order of dictionaries in redLL_data
            redLL_data_reversed = redLL_data.get('value', [])[::-1]

            # Save redLL values into a text file.
            with open(f'{file_path}{server.name}-redLL_data.txt', 'w') as f:
                json.dump({"redLL": redLL_data_reversed}, f)

            # Set dtcReady to 0.
            server.send_to_dcs({
                "command": "setVariable",
                "name": "dtcReady",
                "value": 0
            })

        # Extract the list of coordinates from the jjson file
        with open(f"{file_path}{server.name}-{color.lower()}LL_data.txt", 'r') as f:
            data = json.load(f)
        coordinates = data.get(f"{color.lower()}LL", [])

        # Check if the coordinates data is a list
        if not isinstance(coordinates, list):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"No {color}LL data available.", ephemeral=True)
            return

        # Strip newline characters and split coordinates
        coordinates = [(waypoint['lat'], waypoint['long'], waypoint['alt']) for waypoint in coordinates]

        # Select only the waypoints specified by the user
        coordinate_list = [coordinates[index] for index in waypoint_indices if index < len(coordinates)]

        # Convert the coordinates based on airframe
        converted_list = convert_coordinates(coordinate_list, airframe)

        if airframe == "15":
            json_data = {
                "Waypoints": {
                    "Waypoints": converted_list,
                    "SteerpointStart": 1,
                    "SteerpointEnd": len(converted_list),
                    "OverrideRange": False,
                    "EnableUpload": True
                },
                "Displays": None,
                "Misc": None
            }
        elif airframe == "16":
            json_data = {
                "Waypoints": {
                    "Waypoints": converted_list,
                    "SteerpointStart": 1,
                    "SteerpointEnd": len(converted_list),
                    "OverrideRange": False,
                    "EnableUploadCoordsElevation": True,
                    "EnableUploadTOS": True,
                    "EnableUpload": True
                },
                "Radios": None,
                "CMS": None,
                "MFD": None,
                "HARM": None,
                "HTS": None,
                "Misc": None
            }
        elif airframe == "18":
            json_data = {
                "Waypoints": {
                    "Waypoints": converted_list,
                    "SteerpointStart": 0,
                    "EnableUpload": True
                },
                "Sequences": None,
                "PrePlanned": None,
                "Radios": None,
                "CMS": None,
                "Misc": None
            }
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Error processing data, is the airframe selection right?",
                                                    ephemeral=True)
            return

        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True)
        # Convert the list to a string
        string_data = json.dumps(json_data, ensure_ascii=False)

        # Encode the string
        string_byte_array = string_data.encode('utf-8')

        # Compress the string
        compressed_data = gzip.compress(string_byte_array)

        # Get the length of the uncompressed data
        uncompressed_length = len(string_byte_array)

        # Convert the length to a 4-byte binary format (little-endian)
        length_bytes = struct.pack('<I', uncompressed_length)

        # Append the length bytes to the compressed data
        compressed_data_with_length = length_bytes + compressed_data

        # Encode the data with base64
        encoded_data = base64.b64encode(compressed_data_with_length)

        # Convert bytes to string and remove leading 'b' and quotes
        decoded_data = str(encoded_data)[2:-1]

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(f"""
Copy the following message to your clipboard and use the "Load from Clipboard" function in DTC for DCS, or use the file 
sent to your private messages.
```\n{decoded_data}\n```
        """)

        # Save JSON data to a temporary file
        json_data_string = json.dumps(json_data, indent=2, ensure_ascii=False, cls=DecimalEncoder)

        # Determine the directory of the current script file
        current_dir = os.path.dirname(os.path.realpath(__file__))

        # Create a temporary file in the 'tmp' directory inside the current directory
        fd, temp_file_path = tempfile.mkstemp(suffix=".json", dir=f"{current_dir}/tmp")

        # Close the file descriptor immediately after creating the temporary file
        os.close(fd)

        with open(temp_file_path, 'w', encoding='utf-8') as temp_file:
            temp_file.write(json_data_string)

        # Attempt to send the file
        dm_channel = await interaction.user.create_dm()
        for channel in [dm_channel, interaction.channel]:
            try:
                await channel.send(file=discord.File(temp_file_path))
                if channel == dm_channel:
                    await interaction.followup.send('File sent as a DM.', ephemeral=True)
                else:
                    await interaction.followup.send('Here is your file:')
                break
            except discord.HTTPException:
                continue
        else:
            await interaction.followup.send('File too large. You need a higher boost level for your server.')

        await asyncio.sleep(1)  # wait for 1 second

        # Attempt to remove the temporary file
        try:
            os.remove(temp_file_path)
        except Exception as e:
            self.log.exception(e)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Dtc(bot))
