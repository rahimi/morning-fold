#!/usr/bin/env python3
"""Downloads the embedding model the graph and the passage index use, and verifies every file's checksum.

    python3 tools/get_model.py [--dest DIR] [--split] [--force]

Source and checksums: model/MODEL.json (multilingual-e5-small, int8 ONNX, MIT licence), pinned to one
Hugging Face revision. Default destination: the engine's model/ folder (ignored by git, so the weights
never enter the public repo).

--split writes the ONNX file as e5-small-int8.onnx.part0, .part1 (each under GitHub's 100 MB file limit),
for an instance that commits the model so scheduled cloud runs don't download it every morning:
    python3 engine/tools/get_model.py --dest model --split
The tools look for the model in $MORNING_FOLD_MODEL, then the instance's model/, then the engine's model/.
"""
import hashlib
import json
import os
import sys
import tempfile

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = json.load(open(os.path.join(ENGINE, "model", "MODEL.json"), encoding="utf-8"))
PART = 60_000_000


def sha256(paths_):
    h = hashlib.sha256()
    for p in paths_:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def have(dest, name):
    """The local files that make up `name` (the file itself, or its parts), or []."""
    whole = os.path.join(dest, name)
    if os.path.exists(whole):
        return [whole]
    parts = sorted(f for f in os.listdir(dest) if f.startswith(name + ".part")) if os.path.isdir(dest) else []
    return [os.path.join(dest, f) for f in parts]


def download(url, dst):
    import requests
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(dst, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total and sys.stderr.isatty():
                    sys.stderr.write(f"\r  {done / 1e6:.0f} / {total / 1e6:.0f} MB")
        if total and sys.stderr.isatty():
            sys.stderr.write("\n")


def main(args):
    dest = os.path.abspath(args[args.index("--dest") + 1]) if "--dest" in args else os.path.join(ENGINE, "model")
    split, force = "--split" in args, "--force" in args
    os.makedirs(dest, exist_ok=True)
    base = SPEC["source"].rstrip("/") + "/resolve/" + SPEC["revision"] + "/"
    report = {}
    for name, f in SPEC["files"].items():
        cur = have(dest, name)
        if cur and not force and sha256(cur) == f["sha256"]:
            report[name] = "ok (already there)"
            continue
        fd, tmp = tempfile.mkstemp(dir=dest, prefix=".dl-")
        os.close(fd)
        try:
            print(f"downloading {name} ({f['size'] / 1e6:.1f} MB)", file=sys.stderr)
            download(base + f["from"], tmp)
            got = sha256([tmp])
            if got != f["sha256"]:
                raise SystemExit(f"{name}: checksum mismatch (got {got}, expected {f['sha256']}); nothing written")
            for old in cur:
                os.remove(old)
            if split and os.path.getsize(tmp) > PART:
                with open(tmp, "rb") as src:
                    k = 0
                    while True:
                        b = src.read(PART)
                        if not b:
                            break
                        open(os.path.join(dest, f"{name}.part{k}"), "wb").write(b)
                        k += 1
                os.remove(tmp)
                report[name] = f"ok (downloaded, verified, {k} parts)"
            else:
                os.replace(tmp, os.path.join(dest, name))
                report[name] = "ok (downloaded, verified)"
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    print(json.dumps({"dest": dest, "license": SPEC["license"], "files": report}, indent=1))


if __name__ == "__main__":
    if "-h" in sys.argv or "--help" in sys.argv:
        sys.exit(__doc__)
    main(sys.argv[1:])
