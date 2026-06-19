from .registry import skill_registry
from .builtins.web import WebSearchSkill, WebScrapeSkill
from .builtins.render import RenderHtmlSkill, RenderSvgSkill
from .builtins.sandbox import RunCommandSkill
from .builtins.css_tools import GetUserCssSkill, PatchUserCssSkill, AppendUserCssSkill, SetUserCssSkill
from .builtins.document import EditDocumentSkill
from .builtins.hyperframes import RenderVideoSkill


def register_builtins():
    builtins = [
        WebSearchSkill(),
        WebScrapeSkill(),
        RenderHtmlSkill(),
        RenderSvgSkill(),
        RunCommandSkill(),
        GetUserCssSkill(),
        PatchUserCssSkill(),
        AppendUserCssSkill(),
        SetUserCssSkill(),
        EditDocumentSkill(),
        RenderVideoSkill(),
    ]
    for skill in builtins:
        skill_registry.register(skill)
    return skill_registry
