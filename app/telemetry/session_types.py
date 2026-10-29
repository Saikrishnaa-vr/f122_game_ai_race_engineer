"""F1 22 (2022 UDP format) Session packet type codes.

The Session packet identifies the session, not the game mode. In particular,
there is no dedicated LAN race session type.
"""

SESSION_NAMES = {
    1: "Practice 1", 2: "Practice 2", 3: "Practice 3", 4: "Short practice",
    5: "Qualifying 1", 6: "Qualifying 2", 7: "Qualifying 3",
    8: "Short qualifying", 9: "One-shot qualifying",
    10: "Race", 11: "Race 2", 12: "Race 3", 13: "Time Trial",
}
PRACTICE_SESSION_TYPES = frozenset((1, 2, 3, 4))
QUALIFYING_SESSION_TYPES = frozenset((5, 6, 7, 8, 9))
RACE_SESSION_TYPES = frozenset((10, 11, 12))
TIME_TRIAL_SESSION_TYPES = frozenset((13,))
DRIVING_SESSION_TYPES = frozenset(SESSION_NAMES)
GAME_MODE_NAMES = {
    0: "Event Mode", 3: "Grand Prix", 5: "Time Trial", 6: "Splitscreen",
    7: "Online Custom", 8: "Online League", 11: "Career Invitational",
    12: "Championship Invitational", 13: "Championship",
    14: "Online Championship", 15: "Online Weekly Event",
    19: "Career '22", 20: "Career '22 Online", 127: "Benchmark",
}


def session_name(code: int | None) -> str:
    return SESSION_NAMES.get(code, f"Unknown session ({code})" if code is not None else "Unknown session")


def game_mode_name(code: int | None) -> str:
    return GAME_MODE_NAMES.get(code, f"Unknown mode ({code})" if code is not None else "Unknown mode")
