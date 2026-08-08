"""Tests for the P4 agentic-git engine (pure logic, no Docker required)."""

import os
import tempfile

# Point the DB at a throwaway file BEFORE anything imports app.database, so
# the credential-scrub cache test below can seed rows without touching a
# real database.
_tmp_db = os.path.join(tempfile.mkdtemp(prefix="llmdash_gitdb_"), "test.db")
os.environ.setdefault("DATABASE_PATH", _tmp_db)

import pytest

from app import git_tools
from app.git_tools import (
    GitToolError, CloneUrl,
    sanitize_repo_name, parse_clone_url, clean_https_url, web_url, same_remote,
    bot_identity, _token_parts, _secrets_for, _redact, _cap,
    _parse_log_offenders, _manual_pr_url, _api_base, _ssh_command,
    worktree_path, _clean_auth_error, _basic_auth,
)


class _FakeRepo:
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.name = kw.get("name", "demo")
        self.clone_url = kw.get("clone_url", "git@github.com:acme/demo.git")
        self.access = kw.get("access", "write")
        self.auth_type = kw.get("auth_type", "none")
        self.credential = kw.get("credential", "")
        self.default_branch = kw.get("default_branch", "main")
        self.pr_preferred = kw.get("pr_preferred", True)


# --- sanitize_repo_name ---------------------------------------------------

def test_sanitize_repo_name_ok():
    assert sanitize_repo_name("my.repo_2") == "my.repo_2"
    assert sanitize_repo_name("  hello-world  ") == "hello-world"


@pytest.mark.parametrize("bad", ["", "has space", "a/b", "semi;colon", "quote'", "x" * 65, None])
def test_sanitize_repo_name_rejects(bad):
    with pytest.raises(GitToolError):
        sanitize_repo_name(bad)


# --- parse_clone_url ------------------------------------------------------

def test_parse_https():
    u = parse_clone_url("https://codeberg.org/axoisaxo/LLMDash.git")
    assert u == CloneUrl("https", "codeberg.org", "axoisaxo/LLMDash.git")


def test_parse_https_with_port_and_userinfo():
    u = parse_clone_url("https://user:tok@git.example.com:8443/team/repo.git")
    assert u.host == "git.example.com"
    assert u.port == 8443
    assert u.user == "user"
    assert u.path == "team/repo.git"


def test_clean_https_url_strips_userinfo_and_keeps_port():
    u = parse_clone_url("https://user:tok@git.example.com:8443/team/repo.git")
    assert clean_https_url(u) == "https://git.example.com:8443/team/repo.git"
    u2 = parse_clone_url("https://github.com/a/b.git")
    assert clean_https_url(u2) == "https://github.com/a/b.git"


def test_parse_http():
    u = parse_clone_url("http://git.example.com:3000/team/repo")
    assert u.host == "git.example.com"
    assert u.path == "team/repo"


def test_parse_scp_like():
    u = parse_clone_url("git@github.com:octocat/Hello-World.git")
    assert u == CloneUrl("ssh", "github.com", "octocat/Hello-World.git")


def test_parse_ssh_url():
    u = parse_clone_url("ssh://git@gitlab.com/group/sub/repo.git")
    assert u.kind == "ssh"
    assert u.host == "gitlab.com"
    assert u.path == "group/sub/repo.git"


@pytest.mark.parametrize("bad", ["", "not a url", "ftp://host/x.git", "https:///nohost"])
def test_parse_clone_url_rejects(bad):
    with pytest.raises(GitToolError):
        parse_clone_url(bad)


# --- web_url / same_remote ------------------------------------------------

def test_web_url():
    assert web_url(CloneUrl("ssh", "github.com", "a/b.git")) == "https://github.com/a/b"
    assert web_url(CloneUrl("ssh", "2001:db8::1", "a/b")) == "https://[2001:db8::1]/a/b"


def test_same_remote_ignores_scheme_and_user():
    assert same_remote("https://github.com/a/b.git", "git@github.com:a/b.git")
    assert same_remote("ssh://git@github.com/a/b.git", "https://github.com/a/b.git")
    assert not same_remote("https://github.com/a/b.git", "https://github.com/a/c.git")
    assert not same_remote("https://github.com/a/b.git", "https://gitlab.com/a/b.git")


# --- bot_identity ---------------------------------------------------------

def test_bot_identity_defaults(monkeypatch):
    monkeypatch.setattr(git_tools.app_config.settings, "git_bot_name", "LLMDash")
    monkeypatch.setattr(git_tools.app_config.settings, "git_bot_email", "")
    name, email = bot_identity(CloneUrl("ssh", "codeberg.org", "a/b"))
    assert name == "LLMDash"
    assert email == "llmdash@codeberg.org"  # falls back to the clone host


def test_bot_identity_configured(monkeypatch):
    monkeypatch.setattr(git_tools.app_config.settings, "git_bot_name", "Bot")
    monkeypatch.setattr(git_tools.app_config.settings, "git_bot_email", "bot@example.com")
    name, email = bot_identity(CloneUrl("ssh", "github.com", "a/b"))
    assert (name, email) == ("Bot", "bot@example.com")


# --- credentials / redaction ----------------------------------------------

def test_token_parts():
    assert _token_parts("oauth2:sekrit") == ("oauth2", "sekrit")
    assert _token_parts("user:pw:with:colons") == ("user", "pw:with:colons")
    assert _token_parts("glpat-abc") == ("oauth2", "glpat-abc")
    with pytest.raises(GitToolError):
        _token_parts("   ")


def test_redact_scrubs_tokens_from_output():
    repo = _FakeRepo(auth_type="token", credential="oauth2:super-secret-tok")
    secrets = _secrets_for(repo)
    out = "remote: https://oauth2:super-secret-tok@host/x.git\nAll good\n"
    assert "super-secret-tok" not in _redact(out, secrets)
    assert "***" in _redact(out, secrets)
    # non-credential content untouched
    assert _redact("plain text", ["oauth2:super-secret-tok"]) == "plain text"


def test_cap_truncates():
    assert len(_cap("x" * 1000)) == 1000
    long = _cap("y" * (git_tools.MAX_OUTPUT_CHARS + 50))
    assert len(long) <= git_tools.MAX_OUTPUT_CHARS + 100
    assert "truncated" in long


# --- author enforcement ---------------------------------------------------

def test_parse_log_offenders_empty_when_all_bot():
    log = "\n".join([
        "1111111111111111111111111111111111111111|LLMDash|llmdash@example.com",
        "2222222222222222222222222222222222222222|LLMDash|llmdash@example.com",
    ])
    assert _parse_log_offenders(log, "LLMDash", "llmdash@example.com") == []


def test_parse_log_offenders_flags_user_commits():
    log = "\n".join([
        "1111111111111111111111111111111111111111|LLMDash|llmdash@example.com",
        "2222222222222222222222222222222222222222|Alice|alice@example.com",
        "3333333333333333333333333333333333333333|Bob|bob@example.com",
    ])
    offenders = _parse_log_offenders(log, "LLMDash", "llmdash@example.com")
    assert len(offenders) == 2
    assert all("alice@example.com" in o or "bob@example.com" in o for o in offenders)


def test_parse_log_offenders_tolerates_garbage_lines():
    log = "not-a-valid-line\n1111|LLMDash|llmdash@example.com\n"
    offenders = _parse_log_offenders(log, "LLMDash", "llmdash@example.com")
    assert offenders == []


# --- PR URLs / forge detection -------------------------------------------

def test_manual_pr_urls():
    gh = CloneUrl("ssh", "github.com", "a/b.git")
    assert _manual_pr_url(gh, "feature", "main") == "https://github.com/a/b/compare/main...feature?expand=1"
    gl = CloneUrl("ssh", "gitlab.com", "a/b.git")
    assert "merge_requests/new" in _manual_pr_url(gl, "feature", "main")
    codeberg = CloneUrl("ssh", "codeberg.org", "a/b.git")
    assert _manual_pr_url(codeberg, "feature", "main") == "https://codeberg.org/a/b/compare/main...feature"


def test_api_base_detection():
    assert _api_base(CloneUrl("ssh", "github.com", "a/b")) == ("https://api.github.com", "github")
    assert _api_base(CloneUrl("ssh", "gitlab.com", "a/b")) == ("https://gitlab.com/api/v4", "gitlab")
    assert _api_base(CloneUrl("ssh", "codeberg.org", "a/b")) == ("https://codeberg.org/api/v1", "gitea")


# --- misc plumbing --------------------------------------------------------

def test_ssh_command_scoped_to_repo():
    cmd = _ssh_command(42)
    assert "/root/.ssh/llmdash_42" in cmd
    assert "known_hosts_42" in cmd


def test_worktree_path_sanitizes():
    assert worktree_path("my-repo") == "/work/my-repo"


def test_basic_auth_header():
    import base64
    assert _basic_auth("oauth2", "tok") == "Basic " + base64.b64encode(b"oauth2:tok").decode("ascii")


def test_clean_auth_error():
    assert "bot token" in _clean_auth_error("fatal: could not read Username for 'https://github.com'", "token")
    assert "bot token" not in _clean_auth_error("fatal: could not read Username for 'https://github.com'", "none")
    assert _clean_auth_error("connection reset", "token") == "connection reset"


def test_store_line_is_path_scoped():
    """The credential-store line must include the repo path so the token is
    never offered to other repositories on the same host."""
    repo = _FakeRepo(auth_type="token", credential="oauth2:tok123456", clone_url="https://git.example.com/team/repo.git")
    line = git_tools._store_line(repo)
    assert line == "https://oauth2:tok123456@git.example.com/team/repo.git"
    # port is preserved
    repo2 = _FakeRepo(auth_type="token", credential="oauth2:tok123456", clone_url="https://git.example.com:8443/team/repo.git")
    assert git_tools._store_line(repo2) == "https://oauth2:tok123456@git.example.com:8443/team/repo.git"


def test_require_write_blocks_readonly():
    git_tools.require_write(_FakeRepo(access="write"))  # no raise
    with pytest.raises(GitToolError):
        git_tools.require_write(_FakeRepo(access="read"))


@pytest.mark.parametrize("bad", ["--force", "--force-with-lease", "main:evil", "a..b", "x y", "-b", "", "a~1", "a^2"])
def test_branch_name_rejects_git_option_injection(bad):
    """Branch args must never reach git as options or refspecs."""
    with pytest.raises(GitToolError):
        git_tools._check_branch_name(bad, "source")


@pytest.mark.parametrize("good", ["main", "feature/x", "fix_1.2", "v1.0.0-rc"])
def test_branch_name_accepts_valid_refs(good):
    assert git_tools._check_branch_name(good, "source") == good


async def test_credential_scrub_cache_includes_current_and_history():
    """Regression: all_credentials() must serve current repo credentials plus
    remembered (rotated/cleared) ones, and include the base64 Basic form —
    the sandbox redaction depends on it. Also guards the cache-global bug."""
    from sqlalchemy import delete
    from app.database import init_db, async_session, GitRepo, GitCredentialHistory

    await init_db()
    async with async_session() as sess:
        sess.add(GitRepo(
            name="scrub-me", clone_url="https://git.example.com/a/b.git", access="write",
            auth_type="token", credential="oauth2:live-tok-123456", default_branch="main",
        ))
        sess.add(GitCredentialHistory(secret="dead-tok-654321"))
        await sess.commit()

    git_tools.invalidate_credential_cache()
    creds = await git_tools.all_credentials()
    joined = "\n".join(creds)
    assert "live-tok-123456" in joined
    assert "oauth2:live-tok-123456" in joined
    assert "Basic " in joined  # base64 Basic-auth form is scrubbed too
    assert "dead-tok-654321" in joined  # history stays masked after rotation

    # cleanup so other tests are unaffected
    async with async_session() as sess:
        await sess.execute(delete(GitRepo).where(GitRepo.name == "scrub-me"))
        await sess.execute(delete(GitCredentialHistory))
        await sess.commit()
    git_tools.invalidate_credential_cache()
