#!/usr/bin/env python3
import os
import time

import googleapiclient.discovery
import google.oauth2.service_account as service_account

ZONE = "us-west1-b"
VM1_NAME = "vm1-launcher"
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


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main():
    creds = service_account.Credentials.from_service_account_file(
        "service-credentials.json"
    )
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or "datacenter-lab5-moritz"
    compute = googleapiclient.discovery.build("compute", "v1", credentials=creds)

    image_resp = compute.images().getFromFamily(
        project=SOURCE_IMAGE_PROJECT, family=SOURCE_IMAGE_FAMILY
    ).execute()
    source_disk_image = image_resp["selfLink"]

    # payloads to VM1 via metadata
    vm2_startup = read_file("vm2-startup.sh")
    service_creds_json = read_file("service-credentials.json")
    vm1_code = read_file("vm1-launch-vm2.py")

    vm1_startup = r"""#!/bin/bash
set -euxo pipefail

mkdir -p /srv
cd /srv

curl -fsS "http://metadata/computeMetadata/v1/instance/attributes/vm2-startup-script" \
  -H "Metadata-Flavor: Google" > vm2-startup-script.sh

curl -fsS "http://metadata/computeMetadata/v1/instance/attributes/service-credentials" \
  -H "Metadata-Flavor: Google" > service-credentials.json

curl -fsS "http://metadata/computeMetadata/v1/instance/attributes/vm1-launch-vm2-code" \
  -H "Metadata-Flavor: Google" > vm1-launch-vm2.py

curl -fsS "http://metadata/computeMetadata/v1/instance/attributes/project" \
  -H "Metadata-Flavor: Google" > project.txt

export GOOGLE_CLOUD_PROJECT="$(cat project.txt)"

apt-get update
apt-get install -y python3 python3-pip
pip3 install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib

python3 /srv/vm1-launch-vm2.py
"""

    config = {
        "name": VM1_NAME,
        "machineType": MACHINE_TYPE,
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
        "metadata": {
            "items": [
                {"key": "startup-script", "value": vm1_startup},
                {"key": "vm2-startup-script", "value": vm2_startup},
                {"key": "service-credentials", "value": service_creds_json},
                {"key": "vm1-launch-vm2-code", "value": vm1_code},
                {"key": "project", "value": project},
            ]
        },
    }

    print(f"[CREATE] VM1 '{VM1_NAME}' in {ZONE} ...")
    op = compute.instances().insert(project=project, zone=ZONE, body=config).execute()
    wait_for_zone_op(compute, project, ZONE, op["name"])
    print("[OK] VM1 created. It will now launch VM2 from its startup script.")
    print("Wait ~1-2 minutes, then check instances for VM2 'vm2-flask'.")


if __name__ == "__main__":
    main()