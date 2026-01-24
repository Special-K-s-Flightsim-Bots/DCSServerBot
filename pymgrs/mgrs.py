import math
import re

""""
 * UTM zones are grouped, and assigned to one of a group of 6
 * sets.
 *
 * {int} @private
"""
NUM_100K_SETS = 6

""""
 * The column letters (for easting) of the lower left value, per
 * set.
 *
 * {string} @private
"""
SET_ORIGIN_COLUMN_LETTERS = 'AJSAJS'

""""
 * The row letters (for northing) of the lower left value, per
 * set.
 *
 * {string} @private
"""
SET_ORIGIN_ROW_LETTERS = 'AFAFAF'

A = 65  # A
I = 73  # I
O = 79  # O
V = 86  # V
Z = 90  # Z

""""
 * Conversion from degrees to radians.
 *
 * @private
 * @param {number} deg the angle in degrees.
 * @return {number} the angle in radians.
"""


def degToRad(deg):
    return deg * (math.pi / 180.0)


""""
 * Conversion from radians to degrees.
 *
 * @private
 * @#param {number} rad the angle in radians.
 * @return {number} the angle in degrees.
"""


def radToDeg(rad):
    return 180.0 * (rad / math.pi)


""""
 * Converts a set of Longitude and Latitude co-ordinates to UTM
 * using the WGS84 ellipsoid.
 *
 * @private
 * @param {object} ll Object literal with lat and lon properties
 *     representing the WGS84 coordinate to be converted.
 * @return {object} Object literal containing the UTM value with easting,
 *     northing, zoneNumber and zoneLetter properties, and an optional
 *     accuracy property in digits. Returns null if the conversion failed.
"""


def LLtoUTM(lat, lon):
    Lat = lat
    Long = lon
    a = 6378137.0  # ellip.radius
    eccSquared = 0.00669438  # ellip.eccsq
    k0 = 0.9996
    # LongOrigin
    # eccPrimeSquared
    # N, T, C, A, M
    LatRad = degToRad(Lat)
    LongRad = degToRad(Long)
    # LongOriginRad
    # ZoneNumber
    # (int)
    ZoneNumber = math.floor((Long + 180) / 6) + 1

    # Make sure the longitude 180.00 is in Zone 60
    if Long == 180:
        ZoneNumber = 60

    # Special zone for Norway
    if 56.0 <= Lat < 64.0 and 3.0 <= Long < 12.0:
        ZoneNumber = 32

    # Special zones for Svalbard
    if 72.0 <= Lat < 84.0:
        if 0.0 <= Long < 9.0:
            ZoneNumber = 31
        elif 9.0 <= Long < 21.0:
            ZoneNumber = 33
        elif 21.0 <= Long < 33.0:
            ZoneNumber = 35
        elif 33.0 <= Long < 42.0:
            ZoneNumber = 37

    LongOrigin = (ZoneNumber - 1) * 6 - 180 + 3  # +3 puts origin
    # in middle of
    # zone
    LongOriginRad = degToRad(LongOrigin)

    eccPrimeSquared = eccSquared / (1 - eccSquared)

    N = a / math.sqrt(1 - eccSquared * math.sin(LatRad) * math.sin(LatRad))
    T = math.tan(LatRad) * math.tan(LatRad)
    C = eccPrimeSquared * math.cos(LatRad) * math.cos(LatRad)
    A = math.cos(LatRad) * (LongRad - LongOriginRad)

    M = a * ((
                         1 - eccSquared / 4 - 3 * eccSquared * eccSquared / 64 - 5 * eccSquared * eccSquared * eccSquared / 256) * LatRad - (
                         3 * eccSquared / 8 + 3 * eccSquared * eccSquared / 32 + 45 * eccSquared * eccSquared * eccSquared / 1024) * math.sin(
        2 * LatRad) + (
                         15 * eccSquared * eccSquared / 256 + 45 * eccSquared * eccSquared * eccSquared / 1024) * math.sin(
        4 * LatRad) - (35 * eccSquared * eccSquared * eccSquared / 3072) * math.sin(6 * LatRad))

    UTMEasting = (k0 * N * (A + (1 - T + C) * A * A * A / 6.0 + (
                5 - 18 * T + T * T + 72 * C - 58 * eccPrimeSquared) * A * A * A * A * A / 120.0) + 500000.0)

    UTMNorthing = (k0 * (M + N * math.tan(LatRad) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A * A * A * A / 24.0 + (
                61 - 58 * T + T * T + 600 * C - 330 * eccPrimeSquared) * A * A * A * A * A * A / 720.0)))

    if Lat < 0.0:
        UTMNorthing += 10000000.0  # 10000000 meter offset for
    # southern hemisphere

    # return {'northing': math.round(UTMNorthing), 'easting': math.round(UTMEasting), 'zoneNumber': ZoneNumber, 'zoneLetter': getLetterDesignator(Lat)}

    # fix for python missing math.round function
    return {'northing': int(math.floor(UTMNorthing + 0.5)), 'easting': int(math.floor(UTMEasting + 0.5)),
            'zoneNumber': int(ZoneNumber), 'zoneLetter': getLetterDesignator(Lat)}


""""
 * Calculates the MGRS letter designator for the given latitude.
 *
 * @private
 * @param {number} lat The latitude in WGS84 to get the letter designator
 *     for.
 * @return {char} The letter designator.
"""


def getLetterDesignator(lat):
    # This is here as an error flag to show that the Latitude is
    # outside MGRS limits
    LetterDesignator = 'Z'

    if (84 >= lat) and (lat >= 72):
        LetterDesignator = 'X'

    elif (72 > lat) and (lat >= 64):
        LetterDesignator = 'W'

    elif (64 > lat) and (lat >= 56):
        LetterDesignator = 'V'

    elif (56 > lat) and (lat >= 48):
        LetterDesignator = 'U'

    elif (48 > lat) and (lat >= 40):
        LetterDesignator = 'T'

    elif (40 > lat) and (lat >= 32):
        LetterDesignator = 'S'

    elif (32 > lat) and (lat >= 24):
        LetterDesignator = 'R'

    elif (24 > lat) and (lat >= 16):
        LetterDesignator = 'Q'

    elif (16 > lat) and (lat >= 8):
        LetterDesignator = 'P'

    elif (8 > lat) and (lat >= 0):
        LetterDesignator = 'N'

    elif (0 > lat) and (lat >= -8):
        LetterDesignator = 'M'

    elif (-8 > lat) and (lat >= -16):
        LetterDesignator = 'L'

    elif (-16 > lat) and (lat >= -24):
        LetterDesignator = 'K'

    elif (-24 > lat) and (lat >= -32):
        LetterDesignator = 'J'

    elif (-32 > lat) and (lat >= -40):
        LetterDesignator = 'H'

    elif (-40 > lat) and (lat >= -48):
        LetterDesignator = 'G'

    elif (-48 > lat) and (lat >= -56):
        LetterDesignator = 'F'

    elif (-56 > lat) and (lat >= -64):
        LetterDesignator = 'E'

    elif (-64 > lat) and (lat >= -72):
        LetterDesignator = 'D'

    elif (-72 > lat) and (lat >= -80):
        LetterDesignator = 'C'

    return LetterDesignator


""""
 * Encodes a UTM location as MGRS string.
 *
 * @private
 * @param {object} utm An object literal with easting, northing,
 *     zoneLetter, zoneNumber
 * @param {number} accuracy Accuracy in digits (1-5).
 * @return {string} MGRS string for the given UTM location.
"""


def encode(utm, accuracy):
    # prepend with leading zeroes
    seasting = "00000" + str(utm['easting'])
    snorthing = "00000" + str(utm['northing'])

    return str(utm['zoneNumber']) + utm['zoneLetter'] + str(
        get100kID(utm['easting'], utm['northing'], utm['zoneNumber'])) + seasting[-5:][0:accuracy] + snorthing[-5:][
                                                                                                     0:accuracy]


""""
 * Get the two letter 100k designator for a given UTM easting,
 * northing and zone number value.
 *
 * @private
 * @param {number} easting
 * @param {number} northing
 * @param {number} zoneNumber
 * @return the two letter 100k designator for the given UTM location.
"""


def get100kID(easting, northing, zoneNumber):
    setParm = get100kSetForZone(zoneNumber)
    setColumn = math.floor(easting / 100000)
    setRow = math.floor(northing / 100000) % 20
    return getLetter100kID(setColumn, setRow, setParm)


""""
 * Given a UTM zone number, figure out the MGRS 100K set it is in.
 *
 * @private
 * @param {number} i An UTM zone number.
 * @return {number} the 100k set the UTM zone is in.
"""


def get100kSetForZone(i):
    setParm = i % NUM_100K_SETS
    if setParm == 0:
        setParm = NUM_100K_SETS

    return setParm


""""
 * Get the two-letter MGRS 100k designator given information
 * translated from the UTM northing, easting and zone number.
 *
 * @private
 * @param {number} column the column index as it relates to the MGRS
 *        100k set spreadsheet, created from the UTM easting.
 *        Values are 1-8.
 * @param {number} row the row index as it relates to the MGRS 100k set
 *        spreadsheet, created from the UTM northing value. Values
 *        are from 0-19.
 * @param {number} parm the set block, as it relates to the MGRS 100k set
 *        spreadsheet, created from the UTM zone. Values are from
 *        1-60.
 * @return two letter MGRS 100k code.
"""


def getLetter100kID(column, row, parm):
    # colOrigin and rowOrigin are the letters at the origin of the set
    index = parm - 1
    # colOrigin = SET_ORIGIN_COLUMN_LETTERS.charCodeAt(index)
    colOrigin = ord(SET_ORIGIN_COLUMN_LETTERS[index])

    rowOrigin = ord(SET_ORIGIN_ROW_LETTERS[index])

    # colInt and rowInt are the letters to build to return
    colInt = colOrigin + column - 1
    rowInt = rowOrigin + row
    rollover = False

    if colInt > Z:
        colInt = colInt - Z + A - 1
        rollover = True

    if colInt == I or (colOrigin < I < colInt) or ((colInt > I or colOrigin < I) and rollover):
        colInt += 1

    if colInt == O or (colOrigin < O < colInt) or ((colInt > O or colOrigin < O) and rollover):
        colInt += 1

    if colInt == I:
        colInt += 1

    if colInt > Z:
        colInt = colInt - Z + A - 1

    if rowInt > V:
        rowInt = rowInt - V + A - 1
        rollover = True
    else:
        rollover = False

    if ((rowInt == I) or ((rowOrigin < I) and (rowInt > I))) or (((rowInt > I) or (rowOrigin < I)) and rollover):
        rowInt += 1

    if ((rowInt == O) or ((rowOrigin < O) and (rowInt > O))) or (((rowInt > O) or (rowOrigin < O)) and rollover):
        rowInt += 1

    if rowInt == I:
        rowInt += 1

    if rowInt > V:
        rowInt = rowInt - V + A - 1

    twoLetter = chr(int(colInt)) + chr(int(rowInt))
    return twoLetter


""""
 * Decode the UTM parameters from a MGRS string.
 *
 * @private
 * @param {string} mgrsString an UPPERCASE coordinate string is expected.
 * @return {object} An object literal with easting, northing, zoneLetter,
 *     zoneNumber and accuracy (in meters) properties.
"""


def decode(mgrsString):
    if len(mgrsString) == 0:
        raise "MGRSPoint coverting from nothing"

    length = len(mgrsString)

    hunK = None
    sb = ""
    # testChar
    i = 0

    # get Zone number
    pattern = re.compile("^([0-9]+)[A-Z]")
    match = pattern.match(mgrsString)

    if match:
        sb = match.group(1)
        i = len(sb)

        if i > 2:
            raise ValueError("MGRSPoint bad conversion from: " + mgrsString)
    else:
        raise ValueError("MGRSPoint bad conversion from: " + mgrsString)

    zoneNumber = int(sb)

    if i == 0 or i + 3 > length:
        # A good MGRS string has to be 4-5 digits long,
        # ##AAA/#AAA at least.
        raise ValueError("MGRSPoint bad conversion from: " + mgrsString)

    zoneLetter = mgrsString[i]
    i += 1

    # Should we check the zone letter here? Why not.
    if zoneLetter <= 'A' or zoneLetter == 'B' or zoneLetter == 'Y' or zoneLetter >= 'Z' or zoneLetter == 'I' or zoneLetter == 'O':
        raise ValueError("MGRSPoint zone letter " + zoneLetter + " not handled: " + mgrsString)

    # hunK = mgrsString.substring(i, i += 2)
    hunK = mgrsString[i:i + 2]
    i += 2

    set = get100kSetForZone(zoneNumber)

    east100k = getEastingFromChar(hunK[0], set)
    north100k = getNorthingFromChar(hunK[1], set)

    # We have a bug where the northing may be 2000000 too low.
    # How
    # do we know when to roll over?

    while north100k < getMinNorthing(zoneLetter):
        north100k += 2000000

    # calculate the char index for easting/northing separator
    remainder = length - i

    if remainder % 2 != 0:
        raise ValueError(
            "MGRSPoint has to have an even number \nof digits after the zone letter and two 100km letters - front \nhalf for easting meters, second half for \nnorthing meters" + mgrsString)

    sep = remainder / 2

    sepEasting = 0.0
    sepNorthing = 0.0
    # accuracyBonus, sepEastingString, sepNorthingString, easting, northing
    if sep > 0:
        accuracyBonus = 100000.0 / math.pow(10, sep)
        # sepEastingString = mgrsString.substring(i, i + sep)
        sepEastingString = mgrsString[i:int(i + sep)]
        sepEasting = float(sepEastingString) * accuracyBonus
        sepNorthingString = mgrsString[int(i + sep):]
        sepNorthing = float(sepNorthingString) * accuracyBonus
    else:
        accuracyBonus = 1.0

    easting = sepEasting + east100k
    northing = sepNorthing + north100k

    return {'easting': easting, 'northing': northing, 'zoneLetter': zoneLetter, 'zoneNumber': zoneNumber,
            'accuracy': accuracyBonus}


""""
 * Given the first letter from a two-letter MGRS 100k zone, and given the
 * MGRS table set for the zone number, figure out the easting value that
 * should be added to the other, secondary easting value.
 *
 * @private
 * @param {char} e The first letter from a two-letter MGRS 100?k zone.
 * @param {number} set The MGRS table set for the zone number.
 * @return {number} The easting value for the given letter and set.
"""


def getEastingFromChar(e, set):
    # colOrigin is the letter at the origin of the set for the
    # column
    # curCol = SET_ORIGIN_COLUMN_LETTERS.charCodeAt(set - 1)
    curCol = ord(SET_ORIGIN_COLUMN_LETTERS[set - 1])
    eastingValue = 100000.0
    rewindMarker = False

    while curCol != ord(e[0]):
        curCol += 1
        if curCol == I:
            curCol += 1

        if curCol == O:
            curCol += 1

        if curCol > Z:
            if rewindMarker:
                raise ValueError("Bad character: " + e)

            curCol = A
            rewindMarker = True

        eastingValue += 100000.0

    return eastingValue


""""
 * Given the second letter from a two-letter MGRS 100k zone, and given the
 * MGRS table set for the zone number, figure out the northing value that
 * should be added to the other, secondary northing value. You have to
 * remember that Northings are determined from the equator, and the vertical
 * cycle of letters mean a 2000000 additional northing meters. This happens
 * approx. every 18 degrees of latitude. This method does *NOT* count any
 * additional northings. You have to figure out how many 2000000 meters need
 * to be added for the zone letter of the MGRS coordinate.
 *
 * @private
 * @param {char} n Second letter of the MGRS 100k zone
 * @param {number} set The MGRS table set number, which is dependent on the
 *     UTM zone number.
 * @return {number} The northing value for the given letter and set.
"""


def getNorthingFromChar(n, set):
    if n > 'V':
        raise ValueError("MGRSPoint given invalid Northing " + n)

    # rowOrigin is the letter at the origin of the set for the
    # column
    curRow = ord(SET_ORIGIN_ROW_LETTERS[set - 1])
    northingValue = 0.0
    rewindMarker = False

    while curRow != ord(n[0]):
        curRow += 1
        if curRow == I:
            curRow += 1

        if curRow == O:
            curRow += 1

        # fixing a bug making whole application hang in this loop
        # when 'n' is a wrong character
        if curRow > V:
            if rewindMarker:  # making sure that this loop ends
                raise ValueError("Bad character: " + n)

            curRow = A
            rewindMarker = True

        northingValue += 100000.0

    return northingValue


""""
 * The function getMinNorthing returns the minimum northing value of a MGRS
 * zone.
 *
 * Ported from Geotrans' c Lattitude_Band_Value structure table.
 *
 * @private
 * @param {char} zoneLetter The MGRS zone to get the min northing for.
 * @return {number}
"""


def getMinNorthing(zoneLetter):
    letters = {
        'C': 1100000.0,
        'D': 2000000.0,
        'E': 2800000.0,
        'F': 3700000.0,
        'G': 4600000.0,
        'H': 5500000.0,
        'J': 6400000.0,
        'K': 7300000.0,
        'L': 8200000.0,
        'M': 9100000.0,
        'N': 0.0,
        'P': 800000.0,
        'Q': 1700000.0,
        'R': 2600000.0,
        'S': 3500000.0,
        'T': 4400000.0,
        'U': 5300000.0,
        'V': 6200000.0,
        'W': 7000000.0,
        'X': 7900000.0
    }

    if zoneLetter in letters:
        return letters[zoneLetter]
    else:
        raise ValueError("Invalid zone letter: " + zoneLetter)


""""
 * Converts UTM coords to lat/long, using the WGS84 ellipsoid. This is a convenience
 * class where the Zone can be specified as a single string eg."60N" which
 * is then broken down into the ZoneNumber and ZoneLetter.
 *
 * @private
 * @param {object} utm An object literal with northing, easting, zoneNumber
 *     and zoneLetter properties. If an optional accuracy property is
 *     provided (in meters), a bounding box will be returned instead of
 *     latitude and longitude.
 * @return {object} An object literal containing either lat and lon values
 *     (if no accuracy was provided), or top, right, bottom and left values
 *     for the bounding box calculated according to the provided accuracy.
 *     Returns null if the conversion failed.
"""


def UTMtoLL(utm):
    UTMNorthing = utm['northing']
    UTMEasting = utm['easting']
    zoneLetter = utm['zoneLetter']
    zoneNumber = utm['zoneNumber']
    # check the ZoneNummber is valid
    if zoneNumber < 0 or zoneNumber > 60:
        return None

    k0 = 0.9996
    a = 6378137.0  # ellip.radius
    eccSquared = 0.00669438  # ellip.eccsq
    # eccPrimeSquared
    e1 = (1 - math.sqrt(1 - eccSquared)) / (1 + math.sqrt(1 - eccSquared))
    # N1, T1, C1, R1, D, M
    # LongOrigin
    # mu, phi1Rad

    # remove 500,000 meter offset for longitude
    x = UTMEasting - 500000.0
    y = UTMNorthing

    # We must know somehow if we are in the Northern or Southern
    # hemisphere, this is the only time we use the letter So even
    # if the Zone letter isn't exactly correct it should indicate
    # the hemisphere correctly
    if zoneLetter < 'N':
        y -= 10000000.0  # remove 10,000,000 meter offset used
    # for southern hemisphere

    # There are 60 zones with zone 1 being at West -180 to -174
    LongOrigin = (zoneNumber - 1) * 6 - 180 + 3  # +3 puts origin
    # in middle of
    # zone

    eccPrimeSquared = eccSquared / (1 - eccSquared)

    M = y / k0
    mu = M / (a * (
                1 - eccSquared / 4 - 3 * eccSquared * eccSquared / 64 - 5 * eccSquared * eccSquared * eccSquared / 256))

    phi1Rad = mu + (3 * e1 / 2 - 27 * e1 * e1 * e1 / 32) * math.sin(2 * mu) + (
                21 * e1 * e1 / 16 - 55 * e1 * e1 * e1 * e1 / 32) * math.sin(4 * mu) + (
                          151 * e1 * e1 * e1 / 96) * math.sin(6 * mu)
    # double phi1 = Projmath.radToDeg(phi1Rad);

    N1 = a / math.sqrt(1 - eccSquared * math.sin(phi1Rad) * math.sin(phi1Rad))
    T1 = math.tan(phi1Rad) * math.tan(phi1Rad)
    C1 = eccPrimeSquared * math.cos(phi1Rad) * math.cos(phi1Rad)
    R1 = a * (1 - eccSquared) / math.pow(1 - eccSquared * math.sin(phi1Rad) * math.sin(phi1Rad), 1.5)
    D = x / (N1 * k0)

    lat = phi1Rad - (N1 * math.tan(phi1Rad) / R1) * (
                D * D / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * eccPrimeSquared) * D * D * D * D / 24 + (
                    61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * eccPrimeSquared - 3 * C1 * C1) * D * D * D * D * D * D / 720)
    lat = radToDeg(lat)

    lon = (D - (1 + 2 * T1 + C1) * D * D * D / 6 + (
                5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * eccPrimeSquared + 24 * T1 * T1) * D * D * D * D * D / 120) / math.cos(
        phi1Rad)
    lon = LongOrigin + radToDeg(lon)

    result = {
        'lat': lat,
        'lon': lon
    }

    return result


""""
 * LLtoMGRS converts Latitude, Longtitude value to MGRS string.
 *
 * @private
 * @param {number} lat northing
 * @param {number} lon easting
 * @param {number} accuracy Accuracy in digits (1-5).
 * @return {string} MGRS string for the given geographic location.
"""


def LLtoMGRS(lat, lon):
    return encode(LLtoUTM(lat, lon), 5)


""""
 * MGRStoLL a MGRS string to geographic coordinate (Latitude, Longtitude)
 *
 * @private
 * @param {number} lat northing
 * @param {number} lon easting
 * @param {number} accuracy Accuracy in digits (1-5).
 * @return {number}, {number} latitude, longitude for a given MGRS location.
"""


def MGRStoLL(mgrsString):
    return UTMtoLL(decode(mgrsString))


if __name__ == '__main__':
    # print(getMinNorthing('T'))
    # print(LLtoUTM(37.65398996452414, 44.00628471597047))
    # print(encode(LLtoUTM(37.65398996452414, 44.00628471597047),5))
    # print(UTMtoLL(decode("38SMG12345678")))

    print(LLtoMGRS(39.84389877319336, 29.5625991821))
    print(MGRStoLL("38SLL1234567890"))
