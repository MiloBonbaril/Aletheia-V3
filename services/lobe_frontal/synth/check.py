#!/mnt/forge/dev/Aletheia-V3/venv/bin/python
"""Une vérification de la génération en une commande, pour l'agent de surveillance.

    cd services/lobe_frontal && ./synth/check.py

Une seule commande : l'agent n'a qu'une permission à recevoir, et pas tout Python.
Lecture seule : stats.json, le processus du générateur, vLLM, le GPU, le disque.
"""

import json
import os
import shutil
import subprocess
import urllib.request
from datetime import datetime

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def main() -> None:
    with open(os.path.join(OUT_DIR, "stats.json"), encoding="utf-8") as f:
        stats = json.load(f)
    try:
        os.kill(stats["pid"], 0)
        alive = True
    except OSError:
        alive = False
    try:
        vllm_ok = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).status == 200
    except OSError:
        vllm_ok = False
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
         "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
    now = datetime.now()
    last = stats.get("last_sample_at")
    samples = sum(stats["samples"].values())
    print(json.dumps({
        "now": now.isoformat(timespec="seconds"),
        "process_alive": alive,
        "vllm_ok": vllm_ok,
        "gpu_util_pct,mem_used_mib,mem_total_mib,temp_c": gpu,
        "disk_free_gb": round(shutil.disk_usage(OUT_DIR).free / 1e9, 1),
        "minutes_since_last_sample": round((now - datetime.fromisoformat(last)).total_seconds() / 60, 1) if last else None,
        "samples_total": samples,
        "accepted_ratio": round(stats["accepted"] / samples, 3) if samples else None,
        "stats": stats,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
