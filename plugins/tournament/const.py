from enum import Enum, auto


class TOURNAMENT_PHASE(Enum):
    SIGNUP = auto()
    START_GROUP_PHASE = auto()
    START_ELIMINATION_PHASE = auto()
    MATCH_RUNNING = auto()
    MATCH_FINISHED = auto()
    TOURNAMENT_FINISHED = auto()
