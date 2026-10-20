"""Install or update a customer's skills from the Jurisync repository on GitHub.

    jurisync-setup skills --customer victor

Keeps a git checkout of the repository (``~/jurisync-src`` unless ``--src``), fast-forwards it to the branch the
package was installed from, links each ``customers/<name>/skills/<skill>`` into ``~/.claude/skills`` so Claude Code
reads the checkout directly, and writes one zip per skill to Downloads for apps that take uploads (Cowork). A zip
is rewritten only when its skill changed, and the run says which ones to upload again.

Local edits are never overwritten: a checkout with uncommitted changes or local commits stops the run and lists
them, so the customer decides what to keep.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from importlib import metadata
from pathlib import Path

REPOSITORY = "https://github.com/gabrielccac/jurisync"
DEFAULT_BRANCH = "main"
CUSTOMER_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SKIPPED_FILES = {".DS_Store"}


class SkillsError(ValueError):
    """A stop the customer must resolve; the message says what to do."""


def installed_source():
    """Repository and branch recorded when the package was installed from git (PEP 610), else the defaults."""
    try:
        direct = json.loads(metadata.distribution("jurisync").read_text("direct_url.json") or "{}")
    except (metadata.PackageNotFoundError, json.JSONDecodeError):
        direct = {}
    vcs = direct.get("vcs_info") or {}
    if vcs.get("vcs") != "git":
        return REPOSITORY, DEFAULT_BRANCH
    return direct.get("url") or REPOSITORY, vcs.get("requested_revision") or DEFAULT_BRANCH


def git(*args, cwd=None):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        raise SkillsError(f"git {' '.join(args[:2])} falhou: {(result.stderr or result.stdout).strip()[:300]}")
    return result.stdout.strip()


def update_checkout(src, repository, branch, say):
    """Clone or fast-forward ``src``; stop on local changes instead of discarding them."""
    if not shutil.which("git"):
        raise SkillsError("git não encontrado; instale o git e rode de novo")
    if not (src / ".git").exists():
        if src.exists() and any(src.iterdir()):
            raise SkillsError(f"{src} existe e não é um repositório git; mova ou apague a pasta e rode de novo")
        say(f"Baixando o repositório em {src}...")
        git("clone", "--branch", branch, repository, str(src))
        return git("rev-parse", "--short", "HEAD", cwd=src)
    changed = [
        line[3:]
        for line in git("status", "--porcelain", cwd=src).splitlines()
        if Path(line[3:]).name not in SKIPPED_FILES
    ]
    if changed:
        files = ", ".join(changed)
        raise SkillsError(f"há mudanças locais em {src} que não foram enviadas: {files}. Nada foi alterado")
    git("fetch", "origin", branch, cwd=src)
    if git("rev-parse", "--abbrev-ref", "HEAD", cwd=src) != branch:
        git("checkout", branch, cwd=src)
    ahead = git("rev-list", "--count", f"origin/{branch}..HEAD", cwd=src)
    if ahead != "0":
        raise SkillsError(f"{src} tem {ahead} commit(s) locais que não estão no GitHub. Nada foi alterado")
    before = git("rev-parse", "--short", "HEAD", cwd=src)
    git("merge", "--ff-only", f"origin/{branch}", cwd=src)
    after = git("rev-parse", "--short", "HEAD", cwd=src)
    say(f"Repositório {'já estava atualizado' if before == after else f'atualizado: {before} → {after}'} ({branch}).")
    return after


def customer_skills(src, customer):
    root = src / "customers" / customer / "skills"
    skills = sorted(path for path in root.glob("*") if (path / "SKILL.md").is_file()) if root.is_dir() else []
    if not skills:
        raise SkillsError(f"nenhuma skill do cliente '{customer}' em {root}")
    return skills


def skill_files(skill):
    return sorted(
        path
        for path in skill.rglob("*")
        if path.is_file() and path.name not in SKIPPED_FILES and ".git" not in path.parts
    )


def skill_digest(skill):
    digest = hashlib.sha256()
    for path in skill_files(skill):
        digest.update(path.relative_to(skill).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def points_to(link, target):
    try:
        return link.resolve() == target.resolve() and link.resolve() != link.absolute()
    except OSError:
        return False


def make_link(link, target):
    if sys.platform == "win32":
        import _winapi  # directory junctions need no administrator rights, unlike symlinks

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def link_skill(skill, skills_dir, say):
    """Point ``skills_dir/<skill>`` at the checkout; an existing real folder is kept as a .bak copy."""
    link = skills_dir / skill.name
    if points_to(link, skill):
        return "ok"
    if link.is_symlink() or (link.exists() and link.resolve() != link.absolute()):
        link.unlink() if link.is_symlink() else os.rmdir(link)  # a junction is removed like an empty folder
    elif link.exists():
        backup = link.with_name(f"{link.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        link.rename(backup)
        say(f"  {skill.name}: a cópia antiga foi guardada em {backup}")
    skills_dir.mkdir(parents=True, exist_ok=True)
    make_link(link, skill)
    return "ligada"


def write_zip(skill, target):
    temporary = target.with_name(target.name + ".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{skill.name}/", "")
        for path in skill_files(skill):
            archive.write(path, f"{skill.name}/{path.relative_to(skill).as_posix()}")
    temporary.replace(target)


def load_state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def sync(customer, *, src, skills_dir, zip_dir, state_file, repository, branch, say, make_zips=True):
    """Bring the checkout, the Claude Code links and the upload zips up to date. Returns the skills to re-upload."""
    if not CUSTOMER_NAME.match(customer):
        raise SkillsError(f"nome de cliente inválido: {customer!r}")
    update_checkout(src, repository, branch, say)
    skills = customer_skills(src, customer)
    state = load_state(state_file)
    seen = state.setdefault(customer, {})
    reupload = []
    for skill in skills:
        status = link_skill(skill, skills_dir, say)
        line = f"  {skill.name}: Claude Code {'pronto' if status == 'ok' else status}"
        if make_zips:
            digest, target = skill_digest(skill), zip_dir / f"{skill.name}.zip"
            if seen.get(skill.name) != digest or not target.exists():
                zip_dir.mkdir(parents=True, exist_ok=True)
                write_zip(skill, target)
                reupload.append(skill.name)
                line += f"; zip novo em {target} (envie de novo no Cowork)"
            else:
                line += "; zip sem mudanças"
            seen[skill.name] = digest
        say(line)
    if make_zips:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return reupload


def skills_main(argv, *, say):
    repository, branch = installed_source()
    home = Path.home()
    parser = argparse.ArgumentParser(
        prog="jurisync-setup skills", description="Instala ou atualiza as skills de um cliente a partir do GitHub."
    )
    parser.add_argument("--customer", required=True, help="pasta do cliente em customers/, ex.: victor")
    parser.add_argument("--src", type=Path, default=home / "jurisync-src", help="onde fica o clone do repositório")
    parser.add_argument("--branch", default=os.environ.get("JURISYNC_SKILLS_BRANCH") or branch)
    parser.add_argument("--repository", default=repository)
    parser.add_argument("--skills-dir", type=Path, default=home / ".claude" / "skills")
    parser.add_argument("--zip-dir", type=Path, default=home / "Downloads")
    parser.add_argument("--no-zip", action="store_true", help="não gerar os zips para o Cowork")
    args = parser.parse_args(argv)
    reupload = sync(
        args.customer,
        src=args.src.expanduser(),
        skills_dir=args.skills_dir.expanduser(),
        zip_dir=args.zip_dir.expanduser(),
        state_file=home / ".config" / "jurisync" / "skills-state.json",
        repository=args.repository,
        branch=args.branch,
        say=say,
        make_zips=not args.no_zip,
    )
    if reupload:
        say(f"Reenvie no Cowork: {', '.join(name + '.zip' for name in reupload)}.")
    say("Feche e reabra o Claude para carregar as skills.")
    return 0
