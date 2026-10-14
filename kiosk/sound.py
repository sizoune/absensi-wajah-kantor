"""Optional desktop notification; Windows sound settings control availability."""


def play_result(success: bool) -> None:
    try:
        import winsound
        winsound.PlaySound('SystemAsterisk' if success else 'SystemHand',
                           winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except (ImportError, OSError, RuntimeError):
        pass
