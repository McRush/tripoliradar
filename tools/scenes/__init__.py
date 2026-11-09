"""F-19 scenes for the LoFiGL engine (see view.py)."""

import settings


def engine_help():
    """On-screen help shared by all scenes (keys handled by the engine and the camera); scenes add their own lines."""
    k = settings.label
    move = "".join(k(f'keys_camera.{d}') for d in ('forward', 'left', 'back', 'right', 'up', 'down'))
    return (f"Press '{k('keys_general.labels')}' to hide/show this text\n"
            f"{move} - Camera navigation, {k('keys_camera.zoom_in')}/{k('keys_camera.zoom_out')} - FoV, "
            "Mouse Middle - default FoV")
