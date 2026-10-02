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
