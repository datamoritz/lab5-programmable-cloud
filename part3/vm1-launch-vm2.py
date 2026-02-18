#!/usr/bin/env python3
import os
import time

import googleapiclient.discovery
import google.oauth2.service_account as service_account

ZONE = "us-west1-b"
VM2_NAME = "vm2-flask"
MACHINE_TYPE = f"zones/{ZONE}/machineTypes/e2-medium"
SOURCE_IMAGE_FAMILY = "ubuntu-2204-lts"
SOURCE_IMAGE_PROJECT = "ubuntu-os-cloud"


def wait_for_zone_op(compute, project, zone, op_name):
    while True:
        result = compute.zoneOperations().get(
            project=project, zone=zone, operation=op_name
        ).execute()
        if result.get("status") == "DONE":
            if "error" in result:
                raise RuntimeError(str(result["error"]))
            return
        time.sleep(2)


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main():
    # Runs on VM1 (in /srv)
    creds = service_account.Credentials.from_service_account_file(
        "service-credentials.json"
    )
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT not set on VM1")

    compute = googleapiclient.discovery.build("compute", "v1", credentials=creds)

    image_resp = compute.images().getFromFamily(
        project=SOURCE_IMAGE_PROJECT, family=SOURCE_IMAGE_FAMILY
    ).execute()
    source_disk_image = image_resp["selfLink"]

    vm2_startup = read_text("vm2-startup-script.sh")

    # firewall rule for 5001 (idempotent-ish)
    fw_name = "allow-5001"
    try:
        compute.firewalls().get(project=project, firewall=fw_name).execute()
    except Exception:
        firewall_body = {
            "name": fw_name,
            "direction": "INGRESS",
            "allowed": [{"IPProtocol": "tcp", "ports": ["5001"]}],
            "sourceRanges": ["0.0.0.0/0"],
            "targetTags": ["allow-5001"],
        }
        op = compute.firewalls().insert(project=project, body=firewall_body).execute()
        # global op
        while True:
            r = compute.globalOperations().get(
                project=project, operation=op["name"]
            ).execute()
            if r.get("status") == "DONE":
                if "error" in r:
                    raise RuntimeError(str(r["error"]))
                break
            time.sleep(2)

    config = {
        "name": VM2_NAME,
        "machineType": MACHINE_TYPE,
        "tags": {"items": ["allow-5001"]},
        "disks": [
            {
                "boot": True,
                "autoDelete": True,
                "initializeParams": {"sourceImage": source_disk_image},
            }
        ],
        "networkInterfaces": [
            {
                "network": f"projects/{project}/global/networks/default",
                "accessConfigs": [{"name": "External NAT", "type": "ONE_TO_ONE_NAT"}],
            }
        ],
        "metadata": {"items": [{"key": "startup-script", "value": vm2_startup}]},
    }

    print(f"[CREATE] VM2 '{VM2_NAME}' ...")
    op = compute.instances().insert(project=project, zone=ZONE, body=config).execute()
    wait_for_zone_op(compute, project, ZONE, op["name"])

    inst = compute.instances().get(project=project, zone=ZONE, instance=VM2_NAME).execute()
    ip = (
        inst["networkInterfaces"][0]
        .get("accessConfigs", [{}])[0]
        .get("natIP", "<no external ip>")
    )
    print(f"[OK] VM2 external IP: {ip}")
    print(f"Visit: http://{ip}:5001")


if __name__ == "__main__":
    main()