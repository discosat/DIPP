#!/usr/bin/env python3
"""
Install a staged run-set on the board and verify every file landed intact.

    deploy.py <run-set> [--board-dir /home/root/tmp-experiments]

Refuses to install the daemon without the modules. A3 changed ImageBatch, and
that struct crosses the fork boundary, so a mixed pair silently corrupts every
field after `priority` -- which looks like a data bug, not a deploy bug.

Verification is md5 board-side against md5 computed here: BusyBox has md5sum but
not sha256sum, and an unverified transfer is how a 3200-byte file once arrived
as 464 bytes with nothing complaining.
"""
import argparse, hashlib, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED_MODULES = {
    "libdemosaic.so", "libtile.so", "libtflite_classification.so",
    "libtflite_binary_segmentation.so", "libjpegxl_module.so",
    "libjpeg2000_module.so",
}


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def board(cmd, timeout=300):
    r = subprocess.run([os.path.join(HERE, "board_console.py"), cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_set")
    ap.add_argument("--board-dir", default="/home/root/tmp-experiments")
    a = ap.parse_args()

    staged = os.path.join(HERE, "..", "staging", a.run_set)
    tarball = staged + ".tar.gz"
    manifest_path = os.path.join(staged, "MANIFEST.json")
    for p in (tarball, manifest_path):
        if not os.path.exists(p):
            print(f"missing {p}; run tools/stage.sh {a.run_set} first", file=sys.stderr)
            return 1
    manifest = json.load(open(manifest_path))

    have = set(manifest["artifacts"])
    missing = REQUIRED_MODULES - have
    if "dipp" in have and missing:
        print("REFUSING to deploy: daemon present but modules missing: "
              + ", ".join(sorted(missing)), file=sys.stderr)
        print("A3 changed ImageBatch; a mixed pair corrupts every field after "
              "`priority`.", file=sys.stderr)
        return 1

    print(f"=== deploying '{a.run_set}' ({manifest['build_id']}) ===")
    if manifest.get("dipp_uncommitted") or manifest.get("modules_uncommitted"):
        print("  WARNING: staged from a dirty tree; not reproducible from a commit")

    remote_tar = f"/tmp/{a.run_set}.tar.gz"
    r = subprocess.run([os.path.join(HERE, "board_push.py"), tarball, remote_tar],
                       capture_output=True, text=True, timeout=1800)
    print("  " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
    if "MATCH" not in r.stdout:
        print("  push failed verification", file=sys.stderr)
        return 1

    stage_dir = f"/tmp/stage_{a.run_set}"
    board(f"rm -rf {stage_dir} && mkdir -p {stage_dir} && cd {stage_dir} && "
          f"tar xzf {remote_tar} && echo UNPACKED", 600)

    # Remove before copying: BusyBox cp prompts on overwrite and the prompt
    # silently eats the rest of the command line.
    #
    # One short command per step. The single ~400-character clear and install
    # lines this used to send hung the console driver past their timeouts
    # twice (2026-09-29) although the first had run; the same steps sent short
    # went through at once. The md5 check below is what says it worked.
    steps = [
        "rm -f /usr/share/pipeline/*.so",
        f"cd {a.board_dir} && rm -f dipp dipp_ctl emulate_camera run.sh feed_trace.sh storage.vmem cost.seed *.profile *.env",
        f"cd {stage_dir} && cp *.so /usr/share/pipeline/",
        f"cd {stage_dir} && cp dipp dipp_ctl emulate_camera storage.vmem cost.seed {a.board_dir}/",
        f"cd {stage_dir} && (cp run.sh {a.board_dir}/ 2>/dev/null || true)",
        f"cd {stage_dir} && cp feed_trace.sh {a.board_dir}/",
        f"cd {stage_dir} && cp *.profile *.env {a.board_dir}/",
        "chmod +x /usr/share/pipeline/*.so",
        f"cd {a.board_dir} && chmod +x dipp dipp_ctl emulate_camera run.sh feed_trace.sh 2>/dev/null",
    ]
    for step in steps:
        board(step + "; echo STEP_DONE", 120)

    dest = {}
    for name in manifest["artifacts"]:
        if name.endswith(".so"):
            dest[name] = f"/usr/share/pipeline/{name}"
        elif name in ("dipp", "dipp_ctl", "emulate_camera", "storage.vmem", "cost.seed", "run.sh",
                      "feed_trace.sh") \
                or name.endswith((".profile", ".env")):
            dest[name] = f"{a.board_dir}/{name}"
    out = board("md5sum " + " ".join(dest.values()), 600)
    board_md5 = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 32:
            board_md5[os.path.basename(parts[1])] = parts[0]

    ok = bad = 0
    for name, path in sorted(dest.items()):
        want = md5(os.path.join(staged, name))
        got = board_md5.get(name)
        if got == want:
            ok += 1
        else:
            bad += 1
            print(f"  MISMATCH {name}: board={got} expected={want}")
    print(f"  verified {ok}/{ok + bad} installed files")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
