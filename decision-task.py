# coding: utf8

import os
import sys
import json
import taskcluster

decision_task_id = os.environ["TASK_ID"]
# https://docs.taskcluster.net/docs/reference/workers/docker-worker/docs/features#feature-taskclusterproxy
queue = taskcluster.Queue(options={"baseUrl": "http://taskcluster/queue/v1/"})


def create_task(name, command, image, artifacts=None, dependencies=None, env=None, cache=None,
                scopes=None, features=None):
    env = env or {}
    for k in ["GITHUB_EVENT_CLONE_URL", "GITHUB_EVENT_COMMIT_SHA"]:
        env.setdefault(k, os.environ[k])

    task_id = taskcluster.slugId().decode("utf8")
    payload = {
        "taskGroupId": decision_task_id,
        "dependencies": [decision_task_id] + (dependencies or []),
        "schedulerId": "taskcluster-github",
        "provisionerId": "aws-provisioner-v1",
        "workerType": "servo-docker-worker",

        "created": taskcluster.fromNowJSON(""),
        "deadline": taskcluster.fromNowJSON("1 hour"),
        "metadata": {
            "name": "Taskcluster experiments for Servo: " + name,
            "description": "",
            "owner": os.environ["GITHUB_EVENT_OWNER"],
            "source": os.environ["GITHUB_EVENT_SOURCE"],
        },
        "scopes": scopes or [],
        "payload": {
            "cache": cache or {},
            "maxRunTime": 3600,
            "image": image,
            "command": [
                "/bin/bash",
                "--login",
                "-c",
                """
                    set -e
                    set -x
                    git clone $GITHUB_EVENT_CLONE_URL repo
                    cd repo
                    git checkout $GITHUB_EVENT_COMMIT_SHA
                """ + command
            ],
            "env": env,
            "artifacts": {
                "public/" + artifact_name: {
                    "type": "file",
                    "path": path,
                    "expires": taskcluster.fromNowJSON("1 week"),
                }
                for artifact_name, path in artifacts or []
            },
            "features": features or {},
        },
    }
    queue.createTask(task_id, payload)
    print("Scheduled %s: %s" % (name, task_id))
    return task_id

image_build_task = create_task(
    "docker image build task",
    """
        docker build -t "$IMAGE" "docker/$IMAGE"
        docker save "$IMAGE" | lz4 > /image.tar.lz4
    """,
    env={
        "IMAGE": "servo-x86_64-linux",
    },
    artifacts=[
        ("image.tar.lz4", "/image.tar.lz4"),
    ],

    # https://hub.docker.com/r/servobrowser/image-builder/
    # https://github.com/SimonSapin/servo-docker-image-builder-image
    image="servobrowser/image-builder@sha256:f2370c4b28aa537e47c0cacb82cc53272233fa256b6634c0eebc46e2dd019333",

    features={
        "dind": True,  # docker-in-docker
    },
)

build_task = create_task(
    "build task",
    "./build-task.sh",
    dependencies=[image_build_task],
    image={
        "type": "task-image",
        "taskId": image_build_task,
        "path": "public/image.tar.lz4",
    },

    artifacts=[
        ("executable.gz", "/repo/something-rust/something-rust.gz"),
    ],

    # https://docs.taskcluster.net/docs/reference/workers/docker-worker/docs/caches
    scopes=[
        "docker-worker:cache:cargo-registry-cache",
        "docker-worker:cache:cargo-git-cache",
    ],
    cache={
        "cargo-registry-cache": "/root/.cargo/registry",
        "cargo-git-cache": "/root/.cargo/git",
    },
)

create_task(
    "run task",
    "./run-task.sh",
    image="ubuntu:bionic-20180821@sha256:b5309340de7a9a540cf6c0cba3eabdfb9c9bc5153026d37991fd0028180fc725",
    dependencies=[build_task],
    env={"BUILD_TASK_ID": build_task},
)
