from enum import Enum


class StrafeQuality(Enum):
    POOR_PASS = 1
    INEFFECTIVE_PASS = 2
    GOOD_PASS = 3
    EXCELLENT_PASS = 4
    DEADEYE_PASS = 5


class BombQuality(Enum):
    POOR = 1
    INEFFECTIVE = 2
    GOOD = 3
    EXCELLENT = 4
    SHACK = 5


EMOJIS = {
    None: "❌",
    1: "🟥",
    2: "🟧",
    3: "🟨",
    4: "🟩",
    5: "🎯"
}
