"""Start the local low-resource profile without replacing platform services.

Credentials are read from the existing local gateway container, never printed,
saved in run manifests, or passed on a process command line.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import hashlib

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
ASSET_ROOT = Path(os.environ.get("ANNOTATION_ASSET_ROOT", str(REPO / "annotation/models")))
QWEN_IMAGE = "ghcr.io/ggml-org/llama.cpp@sha256:82de60bb2ba6e66d750f4b7db8752bc3071a2ed3999700d0b21c8b5c709b5513"
QDRANT_IMAGE = "qdrant/qdrant@sha256:45f8e3ddc2570a4d029877e1b5ec1045c19b3852b4e22a55c7f43b05aea0ca89"


def container(name, args):
    old = subprocess.run(["docker", "inspect", name], capture_output=True)
    if old.returncode == 0:
        info = json.loads(old.stdout)[0]
        if info["Config"].get("Labels", {}).get("finevision.annotation-profile") != "cpu-low-v1":
            raise RuntimeError("refusing to replace unowned container " + name)
        if not info["State"]["Running"]:
            subprocess.run(["docker", "start", name], check=True, stdout=subprocess.DEVNULL)
        return
    subprocess.run(["docker", "run", "-d", "--name", name, "--restart", "unless-stopped",
        "--label", "finevision.annotation-profile=cpu-low-v1", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "256", *args],
        check=True, stdout=subprocess.DEVNULL)


def main():
    runtime = ROOT / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    python = runtime / "venv/bin/python"
    models = ASSET_ROOT / "workflow"
    qwen = Path(os.environ.get("ANNOTATION_QWEN_MODEL", str(ASSET_ROOT / "qwen/model.gguf")))
    mmproj = ASSET_ROOT / "qwen/mmproj-F16.gguf"
    sam = Path(os.environ.get("ANNOTATION_SAM_MODEL", str(Path.home() / ".cache/huggingface/hub/models--facebook--sam2.1-hiera-base-plus/snapshots/b7320756a13354e7530a63935656d35b2f91a290")))
    for path in (python, models / "manifest.json", qwen, mmproj, sam / "model.safetensors"):
        if not path.exists():
            raise RuntimeError("missing local component: " + str(path))
    requirements = ROOT / "requirements.txt"
    dependency_marker = runtime / "requirements.sha256"
    requirement_hash = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not dependency_marker.exists() or dependency_marker.read_text().strip() != requirement_hash:
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)], check=True)
        dependency_marker.write_text(requirement_hash)
    container("finevision-annotation-qwen-low", ["--cpus", "4", "--memory", "8g", "-p", "127.0.0.1:8087:8080",
        "-v", str(qwen) + ":/models/model.gguf:ro", "-v", str(mmproj) + ":/models/mmproj.gguf:ro",
        QWEN_IMAGE, "-m", "/models/model.gguf", "--mmproj", "/models/mmproj.gguf", "--host", "0.0.0.0",
        "--port", "8080", "--ctx-size", "4096", "--parallel", "1", "--gpu-layers", "0",
        "--no-mmproj-offload", "--threads", "4", "--threads-batch", "4", "--threads-http", "2",
        "--image-min-tokens", "256", "--image-max-tokens", "768", "--no-webui"])
    container("finevision-annotation-qdrant", ["--cpus", "1", "--memory", "1g", "-p", "127.0.0.1:6335:6333",
        "-v", "finevision-annotation-qdrant:/qdrant/storage", QDRANT_IMAGE])
    pidfile = runtime / "worker.pid"
    if pidfile.exists():
        pid = int(pidfile.read_text())
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
        except FileNotFoundError:
            command = b""
        if str(ROOT / "worker.py").encode() in command:
            print(f"Annotation worker already running: {pid}")
            return
    binary = runtime / "annotation-agent"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/annotation-agent"], cwd=REPO / "go", check=True)
    gateway = json.loads(subprocess.check_output(["docker", "inspect", "finevision-go-llm-gateway-1"]))[0]
    config = dict(item.split("=", 1) for item in gateway["Config"]["Env"])
    python_path = str(REPO / "backend" / "src")
    if os.environ.get("PYTHONPATH"):
        python_path += os.pathsep + os.environ["PYTHONPATH"]
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "PYTHONPATH": python_path}
    control = json.loads(subprocess.check_output(["docker", "inspect", "finevision-go-control-plane-1"]))[0]
    control_env = dict(item.split("=", 1) for item in control["Config"]["Env"])
    env["FINEVISION_WORKER_TOKEN"] = control_env.get("FINEVISION_WORKER_TOKEN", "")
    env.setdefault("FINEVISION_WORKER_ID", "annotation-local")
    env.setdefault("FINEVISION_WORKER_NAME", "本地标注实例")
    for source, target in (("FINEVISION_LLM_BASE_URL", "VLM_BASE_URL"), ("FINEVISION_LLM_MODEL", "VLM_MODEL"),
        ("FINEVISION_LLM_API_KEY", "VLM_API_KEY"), ("FINEVISION_LLM_INTERNAL_TOKEN", "ANNOTATION_WORKER_TOKEN")):
        if not config.get(source):
            raise RuntimeError("gateway configuration missing " + source)
        env[target] = config[source]
    with (runtime / "worker.log").open("ab") as log:
        process = subprocess.Popen([str(python), str(ROOT / "worker.py"), "--root", str(runtime / "state"),
            "--models", str(models), "--sam", str(sam), "--binary", str(binary)],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    # PID is operational state, not a credential or experiment result.
    pidfile.write_text(str(process.pid))
    time.sleep(1)
    if process.poll() is not None:
        raise RuntimeError("worker failed to start; see annotation/runtime/worker.log")
    print(f"Annotation worker started: {process.pid}; UI http://localhost:5173/annotation")


if __name__ == "__main__":
    main()
