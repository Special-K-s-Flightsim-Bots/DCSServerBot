# Author: Bambi
# Credits to magwo (Magnus Wolffelt)

from math import sin, cos, asin, atan, pi, sqrt
from sys import argv, stderr


def get_ship_course_and_speed(wind_direction_deg: float,
                              wind_speed_knots: float,
                              apparent_wind_speed_knots: float) -> tuple[float, float, float]:
    w = wind_speed_knots
    a = apparent_wind_speed_knots
    DA = 9 * pi / 180  # Deck Angle
    V_MIN = 3  # The minimum speed the boat is allowed to go

    # Check if we have too much wind
    if w + cos(DA) * V_MIN > a:
        # Set boat speed to minimum
        v = V_MIN

        # Put apparent wind along the angled deck given v = V_MIN
        C = sqrt(cos(DA) ** 2 / sin(DA) ** 2 + 1)
        theta = asin(v / (w * C)) - asin(-1 / C)
    # Check if we have too little wind
    elif a * sin(DA) > w:
        theta = pi / 2
        v = sqrt(a**2 - w**2)
    else:
        theta = asin(a * sin(DA) / w)
        v = a * cos(DA) - w * cos(theta)

    ship_heading = (540 + (wind_direction_deg + theta * 180 / pi)) % 360

    # Calculate the angle of the apparent wind
    ad = atan(w * sin(theta) / (v + w * cos(theta))) * 180 / pi

    return ship_heading, v, ad


if __name__ == "__main__":
    try:
        wd = float(argv[1])
        w = float(argv[2])
        a = float(argv[3])
    except:
        print(
            f"Please run the script with the required arguments: > python {argv[0]} wind_heading_deg wind_speed_knots required_apparent_wind_knots",
            file=stderr,
        )
        exit(1)

    h, s, ad = get_ship_course_and_speed(wd, w, a)

    print(
        f'{"Ship Heading:":22}{h:4.1f}\n'
        f'{"Ship Speed:":22}{s:4.1f}\n'
        f'{"Apparent wind angle:":22}{ad:4.1f}'
    )
