#!/usr/bin/env python3
import argparse
import time
from typing import Optional

import googleapiclient.discovery
import google.auth
from googleapiclient.errors import HttpError


ZONE_DEFAULT = "us-west1-b"
INSTANCE_DEFAULT = "flask-vm"
FIREWALL_DEFAULT = "allow-5000"
MACHINE_DEFAULT = "e2-medium"   # switch to f1-micro when done


def wait_for_zone_op(compute, project: str, zone: str, op_name: str, poll_sec: float = 1.5):
    while True:
        op = compute.zoneOperations().get(project=project, zone=zone, operation=op_name).execute()
        if op.get("status") == "DONE":
            if "error" in op:
                raise RuntimeError(op["error"])
            return
        time.sleep(poll_sec)


def wait_for_global_op(compute, project: str, op_name: str, poll_sec: float = 1.5):
    while True:
        op = compute.globalOperations().get(project=project, operation=op_name).execute()
        if op.get("status") == "DONE":
            if "error" in op:
                raise RuntimeError(op["error"])
            return
        time.sleep(poll_sec)


def firewall_exists(compute, project: str, name: str) -> bool:
    try:
        compute.firewalls().get(project=project, firewall=name).execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        raise


def ensure_firewall_allow_5000(compute, project: str, name: str):
    if firewall_exists(compute, project, name):
        print(f"[OK] firewall '{name}' already exists")
        return

    body = {
        "name": name,
        "network": f"projects/{project}/global/networks/default",
        "direction": "INGRESS",
        "priority": 1000,
        "sourceRanges": ["0.0.0.0/0"],
        "targetTags": [name],
        "allowed": [{"IPProtocol": "tcp", "ports": ["5000"]}],
    }

    print(f"[CREATE] firewall '{name}' ...")
    op = compute.firewalls().insert(project=project, body=body).execute()
    wait_for_global_op(compute, project, op["name"])
    print(f"[OK] firewall '{name}' created")


def instance_get(compute, project: str, zone: str, name: str) -> Optional[dict]:
    try:
        return compute.instances().get(project=project, zone=zone, instance=name).execute()
    except HttpError as e:
        if e.resp.status == 404:
            return None
        raise


def get_external_ip(inst: dict) -> Optional[str]:
    try:
        return inst["networkInterfaces"][0]["accessConfigs"][0]["natIP"]
    except Exception:
        return None


def startup_script() -> str:
    return r"""#!/bin/bash
set -euxo pipefail

LOG=/var/log/startup-script.log
exec > >(tee -a ${LOG} | logger -t startup-script) 2>&1

apt-get update
apt-get install -y python3 python3-pip git

WORKDIR=/opt/flask-tutorial
mkdir -p ${WORKDIR}
cd ${WORKDIR}

if [ ! -d flask-tutorial ]; then
  git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial
fi

cd flask-tutorial

python3 setup.py install
pip3 install -e .

export FLASK_APP=flaskr
flask init-db

nohup flask run -h 0.0.0.0 -p 5000 &
"""


def create_instance(compute, project: str, zone: str, name: str, machine_type: str, tag: str):
    print(f"[CREATE] instance '{name}' ({machine_type}) in {zone} ...")

    config = {
        "name": name,
        "machineType": f"zones/{zone}/machineTypes/{machine_type}",
        "tags": {"items": [tag]},
        "disks": [{
            "boot": True,
            "autoDelete": True,
            "initializeParams": {
                "sourceImage": "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
            }
        }],
        "networkInterfaces": [{
            "network": f"projects/{project}/global/networks/default",
            "accessConfigs": [{"name": "External NAT", "type": "ONE_TO_ONE_NAT"}]
        }],
        "metadata": {"items": [{"key": "startup-script", "value": startup_script()}]},
    }

    op = compute.instances().insert(project=project, zone=zone, body=config).execute()
    wait_for_zone_op(compute, project, zone, op["name"])
    print(f"[OK] instance '{name}' created")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zone", default=ZONE_DEFAULT)
    parser.add_argument("--instance", default=INSTANCE_DEFAULT)
    parser.add_argument("--firewall", default=FIREWALL_DEFAULT)
    parser.add_argument("--machine-type", default=MACHINE_DEFAULT)
    args = parser.parse_args()

    credentials, project = google.auth.default()
    compute = googleapiclient.discovery.build("compute", "v1", credentials=credentials)

    print(f"[INFO] project={project} zone={args.zone}")

    # 1) firewall
    ensure_firewall_allow_5000(compute, project, args.firewall)

    # 2) instance
    inst = instance_get(compute, project, args.zone, args.instance)
    if inst is None:
        create_instance(compute, project, args.zone, args.instance, args.machine_type, args.firewall)

    # 3) wait for external ip
    print("[WAIT] external IP ...")
    for _ in range(80):
        inst = instance_get(compute, project, args.zone, args.instance)
        ip = get_external_ip(inst) if inst else None
        if ip:
            print(f"[OK] external IP = {ip}")
            print(f"\nVisit: http://{ip}:5000\n")
            return
        time.sleep(2)

    raise RuntimeError("Timed out waiting for external IP")


if __name__ == "__main__":
    main()