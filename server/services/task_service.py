def ensure_unique_task_ids(tasks):
    """确保 tasks 列表中每一个任务的 task_id 绝对唯一，杜绝多节点重复并发冲突"""
    seen_ids = set()
    changed = False
    max_id = 0
    for t in tasks:
        tid = str(t.get("task_id", "")).strip()
        if tid.isdigit():
            max_id = max(max_id, int(tid))

    for t in tasks:
        tid = str(t.get("task_id", "")).strip()
        if not tid or tid in seen_ids:
            max_id += 1
            t["task_id"] = f"{max_id:03d}"
            changed = True
        seen_ids.add(t["task_id"])
    return changed

import os
import csv
import time
import tempfile
from server.config import TASK_FILE, tasks_lock, log_msg

def load_tasks_unlocked():
    tasks = []
    if not os.path.exists(TASK_FILE):
        return tasks
    with open(TASK_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if "output_file" not in r:
                r["output_file"] = ""
            if "worker_node" not in r:
                r["worker_node"] = ""
            tasks.append(r)
    if ensure_unique_task_ids(tasks):
        save_tasks_unlocked(tasks)
    return tasks


def save_tasks_unlocked(tasks):
    fieldnames = ["task_id", "template", "model", "apparel", "scene", "status", "output_file", "worker_node",
                  "prompt_id", "server_url", "final_node", "client_id", "error"]
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=os.path.dirname(TASK_FILE), delete=False) as f:
            tmp_path = f.name
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in tasks:
                writer.writerow({key: r.get(key, "") for key in fieldnames})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, TASK_FILE)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def load_tasks():
    with tasks_lock:
        return load_tasks_unlocked()


def save_tasks(tasks):
    with tasks_lock:
        save_tasks_unlocked(tasks)


def reserve_task_ids_unlocked(tasks, count=0):
    """调用者持有 tasks_lock；清空记录后也不复用旧编号及成品路径。"""
    sequence_file = TASK_FILE + ".sequence"
    highest = max((int(t["task_id"]) for t in tasks
                   if str(t.get("task_id", "")).isdigit()), default=0)
    if os.path.exists(sequence_file):
        with open(sequence_file, encoding="utf-8") as f:
            highest = max(highest, int(f.read().strip()))
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=os.path.dirname(TASK_FILE), delete=False) as f:
            tmp_path = f.name
            f.write(str(highest + count))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, sequence_file)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
    return [f"{highest + i:03d}" for i in range(1, count + 1)]


def get_next_pending_task(srv_name):
    """原子认领下一个待处理任务 (严格加锁，绝不重复领单)"""
    with tasks_lock:
        tasks = load_tasks_unlocked()
        for t in tasks:
            if t.get("status", "").upper() in ["PENDING", "WAITING"]:
                t["status"] = "QUEUED"
                t["worker_node"] = srv_name
                save_tasks_unlocked(tasks)
                return dict(t)
        return None


def update_task_status(task_id, new_status, output_file=None, worker_node=None, **fields):
    """原子更新任务状态与产物"""
    with tasks_lock:
        tasks = load_tasks_unlocked()
        updated = False
        for t in tasks:
            if t.get("task_id") == task_id:
                t["status"] = new_status
                if output_file is not None:
                    t["output_file"] = output_file
                if worker_node is not None:
                    t["worker_node"] = worker_node
                t.update(fields)
                updated = True
                break
        if updated:
            save_tasks_unlocked(tasks)


def recover_interrupted_tasks():
    """An interrupted submission may still be executing; never automatically resubmit it."""
    with tasks_lock:
        records = load_tasks_unlocked()
        changed = False
        for record in records:
            if record.get('status') in {'QUEUED', 'SUBMITTING', 'RUNNING', 'DOWNLOADING'}:
                record.update(status='REVIEW', error='服务中断，请先检查回传或核对执行端口，避免重复生成。')
                changed = True
        if changed:
            save_tasks_unlocked(records)

