from app.driver_identity import driver_code, team_identity


def test_driver_codes_cover_2022_roster_and_missing_names() -> None:
    assert driver_code("LECLERC", 19) == "LEC"
    assert driver_code("PÉREZ", 3) == "PER"
    assert driver_code("SCHUMACHER", 11) == "MSC"
    assert driver_code("GUANYU", 16) == "ZHO"
    assert driver_code("Lewis Hamilton", 5) == "HAM"
    assert driver_code(None, 5) == "C06"
    assert driver_code(None, 19, True) == "YOU"


def test_unknown_team_uses_neutral_colour() -> None:
    assert team_identity(1)[0] == "Ferrari"
    assert team_identity(255) == ("Team unknown", "#718395", "#ffffff")
