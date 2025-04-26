def create_tournament_matches(squadrons: list[tuple[int, float]]) -> list[tuple[int, int]]:
    """
    Create tournament matches using snake pairing system.

    Args:
        squadrons: List of tuples (squadron_id, trueskill_rating)

    Returns:
        List of tuples (squadron1_id, squadron2_id) representing matches
    """
    # Sort squadrons by TrueSkill rating in descending order
    sorted_squadrons = sorted(squadrons, key=lambda x: x[1], reverse=True)

    # Check if we have an even number of squadrons
    if len(sorted_squadrons) % 2 != 0:
        raise ValueError("Need an even number of squadrons for the tournament")

    matches = []
    n = len(sorted_squadrons)
    half = n // 2

    # Create matches using snake pairing
    for i in range(half):
        squad1 = sorted_squadrons[i][0]  # Get squadron_id from tuple
        squad2 = sorted_squadrons[-(i + 1)][0]  # Get opponent from bottom, moving upwards
        matches.append((squad1, squad2))

    return matches
