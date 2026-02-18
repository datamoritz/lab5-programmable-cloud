#!/usr/bin/env python3
import argparse
import time
from datetime import datetime

import googleapiclient.discovery
import google.auth
from googleapiclient.errors import HttpError


ZONE_DEFAULT = "us-west1-b"
BASE_INSTANCE_DEFAULT = "flask-vm"
SNAPSHOT_PREFIX = "base-snapshot"
FIREWALL_TAG = "allow-5000"   # keep clones accessible on :5000


def wait_for_zone_op(compute, project: str, zone: str, op_name: str, poll_sec: float = 1.5):
    while True:
        op = compute.zoneOperations().get(project=project, zone=zone, operation=op_name).execute()
        if op.get("status") == "DONE":
            if "error" in op:
                raise RuntimeError(op["error"])
            return op
        time.sleep(poll_sec)


def wait_for_global_op(compute, project: str, op_name: str, poll_sec: float = 1.5):
    while True:
        op = compute.globalOperations().get(project=project, operation=op_name).execute()
        if op.get("status") == "DONE":
            if "error" in op:
                raise RuntimeError(op["error"])
            return op
        time.sleep(poll_sec)


def instance_get(compute, project: str, zone: str, name: str):
    return compute.instances().get(project=project, zone=zone, instance=name).execute()


def snapshot_get(compute, project: str, name: str):
    try:
        return compute.snapshots().get(project=project, snapshot=name).execute()
    except HttpError as e:
        if e.resp.status == 404:
            return None
        raise


def create_snapshot_from_instance_boot_disk(compute, project: str, zone: str, base_instance: str) -> str:
    inst = instance_get(compute, project, zone, base_instance)

    # boot disk is usually the first disk; find the one marked boot=True
    boot_disk = None
    for d in inst.get("disks", []):
        if d.get("boot"):
            boot_disk = d
            break
    if not boot_disk:
        raise RuntimeError("Could not find boot disk on instance")

    # disk source looks like: .../zones/us-west1-b/disks/<diskname>
    disk_source = boot_disk["source"]
    disk_name = disk_source.split("/")[-1]

    snapshot_name = f"{SNAPSHOT_PREFIX}-{base_instance}"

    if snapshot_get(compute, project, snapshot_name):
        print(f"[OK] snapshot '{snapshot_name}' already exists")
        return snapshot_name

    body = {"name": snapshot_name}

    print(f"[CREATE] snapshot '{snapshot_name}' from disk '{disk_name}' ...")
    op = compute.disks().createSnapshot(project=project, zone=zone, disk=disk_name, body=body).execute()
    wait_for_zone_op(compute, project, zone, op["name"])
    print(f"[OK] snapshot '{snapshot_name}' created")
    return snapshot_name


def instance_exists(compute, project: str, zone: str, name: str) -> bool:
    try:
        compute.instances().get(project=project, zone=zone, instance=name).execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        raise


def create_instance_from_snapshot(compute, project: str, zone: str, name: str, snapshot_name: str, machine_type: str):
    if instance_exists(compute, project, zone, name):
        print(f"[OK] instance '{name}' already exists (skip create)")
        return 0.0

    cfg = {
        "name": name,
        "machineType": f"zones/{zone}/machineTypes/{machine_type}",
        "tags": {"items": [FIREWALL_TAG]},
        "disks": [{
            "boot": True,
            "autoDelete": True,
            "initializeParams": {
                # boot from snapshot
                "sourceSnapshot": f"projects/{project}/global/snapshots/{snapshot_name}"
            }
        }],
        "networkInterfaces": [{
            "network": f"projects/{project}/global/networks/default",
            "accessConfigs": [{"name": "External NAT", "type": "ONE_TO_ONE_NAT"}]
        }],
    }

    print(f"[CREATE] instance '{name}' from snapshot '{snapshot_name}' ...")
    t0 = time.perf_counter()
    op = compute.instances().insert(project=project, zone=zone, body=cfg).execute()
    wait_for_zone_op(compute, project, zone, op["name"])
    t1 = time.perf_counter()
    elapsed = t1 - t0
    print(f"[OK] instance '{name}' created in {elapsed:.2f}s")
    return elapsed


def write_timing_md(path: str, base_instance: str, zone: str, machine_type: str, times: list[tuple[str, float]]):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append(f"# Part 2 Timing\n")
    lines.append(f"- Base instance: `{base_instance}`\n")
    lines.append(f"- Zone: `{zone}`\n")
    lines.append(f"- Machine type: `{machine_type}`\n")
    lines.append(f"- Measured: `{now}`\n\n")
    lines.append("| Instance | Create time (s) |\n")
    lines.append("|---|---:|\n")
    for name, sec in times:
        lines.append(f"| `{name}` | {sec:.2f} |\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"[OK] wrote {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", default=ZONE_DEFAULT)
    ap.add_argument("--base-instance", default=BASE_INSTANCE_DEFAULT)
    ap.add_argument("--machine-type", default="e2-medium")  # can switch to f1-micro if desired
    ap.add_argument("--count", type=int, default=3)
    args = ap.parse_args()

    credentials, project = google.auth.default()
    compute = googleapiclient.discovery.build("compute", "v1", credentials=credentials)

    print(f"[INFO] project={project} zone={args.zone}")
    snapshot_name = create_snapshot_from_instance_boot_disk(compute, project, args.zone, args.base_instance)

    times = []
    for i in range(1, args.count + 1):
        name = f"{args.base_instance}-clone-{i}"
        sec = create_instance_from_snapshot(compute, project, args.zone, name, snapshot_name, args.machine_type)
        times.append((name, sec))

    # write into part2/TIMING.md (script is usually run from part2/)
    write_timing_md("TIMING.md", args.base_instance, args.zone, args.machine_type, times)


if __name__ == "__main__":
    main()