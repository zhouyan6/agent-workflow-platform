# bridge/runs.py
"""Run 工作流持久化（ROOT/runs.json）。"""
import json
import os
import uuid
from datetime import datetime


class Runs:
    def __init__(self, root: str):
        self.path = os.path.join(root, "runs.json")

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"runs": []}

    def _save(self, data: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def create(self, conv: str, goal: str, stages: list) -> dict:
        data = self._load()
        run = {"id": uuid.uuid4().hex[:8], "conv": conv, "goal": goal,
               "stages": [{"title": s.get("title", ""), "status": "pending"}
                          for s in (stages or [])],
               "updated": self._now()}
        data["runs"].append(run)
        self._save(data)
        return run

    def update(self, run_id: str, stages: list) -> dict | None:
        data = self._load()
        for run in data["runs"]:
            if run["id"] == run_id:
                run["stages"] = [{"title": s.get("title", ""),
                                  "status": s.get("status", "pending")}
                                 for s in (stages or [])]
                run["updated"] = self._now()
                self._save(data)
                return run
        return None

    def for_conv(self, conv: str) -> list[dict]:
        return [r for r in self._load()["runs"] if r["conv"] == conv]
