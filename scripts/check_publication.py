"""Validate the public publication without contacting Nova or using credentials."""

import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "publication.json").read_text())
raw = "https://raw.githubusercontent.com/youetech/nova-job-search/main/"
for name in manifest["files"]:
    path = root / name
    assert path.is_file(), f"Missing published file: {name}"
    text = path.read_text()
    if name.endswith(".py"):
        compile(text, name, "exec")
        continue
    assert not re.search(r"\{(?:base_url|welcome|continuation|calling|confidentiality|build)\}", text), name
    assert "Playbook build" not in text, name
    assert "Confidential playbook" not in text, name
    if not name.startswith("dev/"):
        assert "dev-hiring-api.usenova.work" not in text, name
    else:
        assert "https://hiring-api.usenova.work" not in text, name
    for match in re.finditer(re.escape(raw) + r"([A-Za-z0-9_./-]+)", text):
        target = match[1].rstrip(".")
        assert (root / target).is_file(), f"Broken link in {name}: {target}"
for prefix in ("", "dev/"):
    index = json.loads((root / prefix / "index.json").read_text())
    assert len([p for p in index["assets"] if p.startswith("skills/")]) == 13
    hosts = json.loads((root / prefix / "hosts.json").read_text())["data"]
    for host in hosts:
        for side in ("shared", "candidate", "recruiter"):
            assert f"hosts/{host['key']}/{side}/skill.md" in index["assets"]
print(f"Validated {len(manifest['files'])} published assets and discovery files")

# Scan only tracked publication files; do not inspect the publisher's local secrets.
import subprocess
tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root, text=True).split("\0")
for name in filter(None, tracked):
    assert not any(part in {".env", ".env.local", ".env.production", "terraform.tfstate"} for part in Path(name).parts), name
    if name == "scripts/check_publication.py":
        continue  # Scanner definitions contain the markers they detect.
    text = (root / name).read_text()
    forbidden = (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
        r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b",
        r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b",
        r"arn:aws:[^\s]+",
        r"/(?:home|Users)/[^/\s]+/",
        r"Playbook build[: ]+[A-Za-z0-9_-]{12,}",
    )
    for pattern in forbidden:
        assert not re.search(pattern, text), f"Private-content marker in {name}"
for name in ("SKILL.md", "skills/nova-room/SKILL.md"):
    text = (root / name).read_text()
    assert text.startswith("---\nname: ") and "\ndescription: " in text, name
    assert "skills/shared/notifications.md" in text and "AWS IoT" in text, name
print("Validated installable entrypoints and private-content guard")
