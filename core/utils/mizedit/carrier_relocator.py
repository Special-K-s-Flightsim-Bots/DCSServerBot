# Credits to magwo (Magnus Wolffelt)
import logging
import math

from core.utils.helper import DictWrapper
from .trigonometric_carrier_cruise import get_ship_course_and_speed
from .me_utils import Speed, Distance, Heading, knots, HeadingAndSpeed

__all__ = ['relocate_carrier']

logger = logging.getLogger(__name__)


#
# TAKEN FROM PyDCS! >>> START
#
def point_from_heading(_x: float, _y: float, heading: float, distance: float) -> tuple[float, float]:
    """Calculates a point from a given point, heading and distance.

    :param _x: source point x
    :param _y: source point y
    :param heading: heading in degrees from source point
    :param distance: distance from source point
    :return: returns a tuple (x, y) of the calculated point
    """
    while heading < 0:
        heading += 360
    heading %= 360
    rad_heading = math.radians(heading)
    x = _x + math.cos(rad_heading) * distance
    y = _y + math.sin(rad_heading) * distance

    return x, y


def heading_between_points(x1: float, y1: float, x2: float, y2: float) -> float:
    """Returns the angle between 2 points in degrees.

    :param x1: x coordinate of point 1
    :param y1: y coordinate of point 1
    :param x2: x coordinate of point 2
    :param y2: y coordinate of point 2
    :return: angle in degrees
    """
    def angle_trunc(a):
        while a < 0.0:
            a += math.pi * 2
        return a
    deltax = x2 - x1
    deltay = y2 - y1
    return math.degrees(angle_trunc(math.atan2(deltay, deltax)))


def distance_to_point(x1: float, y1: float, x2: float, y2: float) -> float:
    """Returns the distance between 2 points

    :param x1: x coordinate of point 1
    :param y1: y coordinate of point 1
    :param x2: x coordinate of point 2
    :param y2: y coordinate of point 2
    :return: distance in point units(m)
    """
    return math.hypot(x2 - x1, y2 - y1)
#
# TAKEN FROM PyDCS <<<< END
#

def get_carrier_cruise(wind: dict, deck_angle: float, desired_apparent_wind: Speed) -> HeadingAndSpeed:
    wind_speed = Speed.from_meters_per_second(wind.get('speed', 0))
    heading, speed, apparent_wind_angle = get_ship_course_and_speed(
        wind.get('dir', 0), wind_speed.knots, desired_apparent_wind.knots
    )
    # Quick hack for Tarawa
    if deck_angle == 0:
        wind_heading = Heading(wind.get('dir', 0))
        heading = wind_heading.opposite.degrees

    solution = HeadingAndSpeed(
        Heading.from_degrees(heading), Speed.from_knots(speed)
    )
    return solution


def rotate_group_around(group: DictWrapper, pivot: tuple[float, float], degrees_change: float):
    # My fear was that using sin/cos would result in incorrect
    # transforms when not near the equator. Polar coordinates (heading + distance)
    # should resolve that, if the Point functions are implemented correctly.
    # I think they're not, but this code at least doesn't prevent correct transform.
    # Maybe DCS doesn't even use mercator projection.
    for unit in group.units:
        distance = distance_to_point(pivot[0], pivot[1], unit.x, unit.y)
        heading = heading_between_points(pivot[0], pivot[1], unit.x, unit.y)
        new_heading = Heading.from_degrees(heading + degrees_change).degrees

        unit.x, unit.y = point_from_heading(pivot[0], pivot[1], new_heading, distance)
        unit.heading = Heading.from_degrees(unit.heading + degrees_change).radians


def relocate_carrier(_: dict, reference: dict, **kwargs):
    # create a wrapper to make it easier (and to mainly keep the old code)
    group = DictWrapper(reference)
    route = group.route

    wind = kwargs.get('wind', {})
    carrier = group.units[0]
    deck_angle = 0 if carrier.type in ['LHA_Tarawa', 'Essex', 'hms_invincible'] else -9.12
    cruise = get_carrier_cruise(wind, deck_angle, Speed.from_knots(25))

    radius = Distance.from_nautical_miles(kwargs.get('radius', 50))
    carrier_start_pos = point_from_heading(group.x, group.y, cruise.heading.opposite.degrees, radius.meters)
    carrier_end_pos = point_from_heading(group.x, group.y, cruise.heading.degrees, radius.meters)

    group_heading_before_change = heading_between_points(
        route.points[0].x, route.points[0].y, route.points[1].x, route.points[1].y
    )
    group_position_before_change = (route.points[0].x, route.points[0].y)

    # we need at least 4 waypoints for the group
    while len(route.points) < 4:
        route.points.append(route.points[-1].clone())

    # change the waypoints
    route.points[0].x = carrier_start_pos[0]
    route.points[0].y = carrier_start_pos[1]
    route.points[0].speed = cruise.speed.meters_per_second

    route.points[1].x = carrier_end_pos[0]
    route.points[1].y = carrier_end_pos[1]
    route.points[1].ETA_locked = False
    route.points[1].speed = cruise.speed.meters_per_second
    route.points[1].speed_locked = True

    route.points[2].x = carrier_start_pos[0]
    route.points[2].y = carrier_start_pos[1]
    route.points[2].speed = knots(50).meters_per_second
    route.points[2].ETA_locked = False
    route.points[2].speed_locked = True

    route.points[3].x = carrier_end_pos[0]
    route.points[3].y = carrier_end_pos[1]
    route.points[3].ETA_locked = False
    route.points[3].speed = cruise.speed.meters_per_second
    route.points[3].speed_locked = True

    position_change = (
        carrier_start_pos[0] - group_position_before_change[0],
        carrier_start_pos[1] - group_position_before_change[1]
    )
    for unit in group.units:
        unit.x += position_change[0]
        unit.y += position_change[1]

    heading_change = cruise.heading.degrees - group_heading_before_change
    rotate_group_around(
        group, (route.points[0].x, route.points[0].y), heading_change
    )

    # change the real thing
    reference['x'] = carrier_start_pos[0]
    reference['y'] = carrier_start_pos[1]
    reference['route'] = route.to_dict()
    reference['units'] = [unit.to_dict() for unit in group.units]
