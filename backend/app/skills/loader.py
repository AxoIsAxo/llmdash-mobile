from .registry import skill_registry
from .builtins.web import WebSearchSkill, WebScrapeSkill
from .builtins.render import RenderHtmlSkill, RenderSvgSkill
from .builtins.sandbox import RunCommandSkill
from .builtins.css_tools import GetUserCssSkill, PatchUserCssSkill, AppendUserCssSkill, SetUserCssSkill, GetThemeSkill, PatchThemeSkill, ResetThemeSkill
from .builtins.document import EditDocumentSkill
from .builtins.hyperframes import RenderVideoSkill
from .builtins.git import GitCloneSkill, GitStatusSkill, GitDiffSkill, GitCommitSkill, GitPushSkill, GitPrSkill


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
        GetThemeSkill(),
        PatchThemeSkill(),
        ResetThemeSkill(),
        EditDocumentSkill(),
        RenderVideoSkill(),
        GitCloneSkill(),
        GitStatusSkill(),
        GitDiffSkill(),
        GitCommitSkill(),
        GitPushSkill(),
        GitPrSkill(),
    ]
    for skill in builtins:
        skill_registry.register(skill)
    return skill_registry
