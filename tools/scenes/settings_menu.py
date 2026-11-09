"""The Settings part of the main menu, made from settings.SCHEMA: a page per ini section, each value an Item with a
SettingValue (scenes/menu.py shows and changes it), "Reset this page" at the end of each page, "Reset all" at the end.
"""
import settings
from scenes.menu import Item

PAGES = ('display', 'graphics', 'camera', 'live')
KEY_PAGES = ('keys_general', 'keys_camera', 'keys_world', 'keys_live', 'keys_replay', 'keys_models')


class SettingValue:
    """One setting as a menu value (see scenes/menu.py Item)."""
    def __init__(self, key):
        self.key = key
        self.setting = settings.BY_KEY[key]
        self.is_key = self.setting.kind == 'key'
        s = self.setting
        default = s.describe(s.default)
        self.note = "   ".join(part for part in (s.help, f"default {default}",
                                                  "applies after a restart" if s.restart else "") if part)

    def text(self):
        return self.setting.describe(settings.get(self.key))

    def changed(self):
        return not settings.is_default(self.key)

    def step(self, d):
        settings.step(self.key, d)

    def reset(self):
        settings.reset(self.key)

    def bind(self, code):
        return settings.bind(self.key, code)


def page_items(section):
    title = settings.SECTION_TITLES[section]

    def reset(app):
        settings.reset_section(section)
        app.scene.say(f"{title}: back to the defaults")

    items = [Item(s.label, value=SettingValue(s.key)) for s in settings.SCHEMA if s.section == section]
    return items + [Item("Reset this page to the defaults", reset)]


def page(section, title=None):
    return Item(title or settings.SECTION_TITLES[section], children=lambda: page_items(section))


def settings_items():
    def reset_all(app):
        settings.reset_all()
        app.scene.back()
        app.scene.say("All settings are back to the defaults")

    return [
        *[page(section) for section in PAGES],
        Item("Keys", children=[page(section, settings.SECTION_TITLES[section].split(": ", 1)[1].capitalize())
                               for section in KEY_PAGES]),
        Item("Reset all settings", children=[
            Item("Yes, put every setting back to its default", reset_all),
            Item("No", lambda app: app.scene.back()),
        ]),
    ]
