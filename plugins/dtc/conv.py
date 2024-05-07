from decimal import Decimal


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


def convert_coordinates(coordinates):
    converted_coordinates = []
    for i, coordinate in enumerate(coordinates, 1):
        latitude = Decimal(coordinate[0])
        longitude = Decimal(coordinate[1])
        elevation = round(float(coordinate[2]) * 3.28084)  # Convert meters to feet and round
        latitude_degrees, latitude_decimal_minutes = ddm_to_dmm(latitude)
        longitude_degrees, longitude_decimal_minutes = ddm_to_dmm(longitude)
        formatted_latitude = format_latitude(latitude_degrees, latitude_decimal_minutes)
        formatted_longitude = format_longitude(longitude_degrees, longitude_decimal_minutes)
        converted_coordinates.append({
            "Sequence": i,
            "Name": f"WPT {i}",
            "Latitude": formatted_latitude,
            "Longitude": formatted_longitude,
            "Elevation": elevation,
            "Target": True,
            "IsCoordinateBlank": False
        })
    return converted_coordinates
