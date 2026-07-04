"""team_config.py — команды goo.network.

Profile ID и User Key теперь могут быть глобальными (задаёт админ в
GlobalSettings) — это полезно, если все операторы бота работают с одним
профильом команды. Per-user UserSettings переопределяют глобальные
(если заданы) — для персональных оверрайдов.
"""

TEAM_KEYS = {
    "Tsum": "7bc1926a-a6ca-46f1-811b-15a09c716c8a",
    "Nurrp": "cd210d0d-05b6-42a0-a403-f3ab1a16d4cd",
}


def get_team_key(team_code: str) -> str:
    return TEAM_KEYS.get(team_code, "")


def profile_attr_name(team_code: str) -> str:
    return f"profile_id_{team_code.lower()}"


def user_key_attr_name(team_code: str) -> str:
    return f"user_key_{team_code.lower()}"


def resolve_profile_id(settings, team_code: str, global_settings=None) -> str:
    """Возвращает Profile ID для команды.

    Приоритет:
      1. Per-user UserSettings.{profile_id_<team>} (персональный оверрайд)
      2. Per-user UserSettings.profile_id (старое поле, fallback)
      3. GlobalSettings.profile_id_<team> (задается админом)
    """
    attr = profile_attr_name(team_code)
    per_user = getattr(settings, attr, "") or ""
    if per_user:
        return per_user
    legacy = getattr(settings, "profile_id", "") or ""
    if legacy:
        return legacy
    # Глобальный
    if global_settings is not None:
        g = getattr(global_settings, attr, "") or ""
        if g:
            return g
    return ""


def get_user_key_for_team(settings, team_code: str, global_settings=None) -> str:
    """Возвращает User Key для команды.

    Приоритет: per-user → global.
    """
    attr = user_key_attr_name(team_code)
    per_user = getattr(settings, attr, "") or ""
    if per_user:
        return per_user
    if global_settings is not None:
        g = getattr(global_settings, attr, "") or ""
        if g:
            return g
    return ""
