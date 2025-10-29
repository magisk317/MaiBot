import json
import os
import time
import atexit

from threading import RLock
from typing import Dict, Any


class ModelPerformanceCache:
    """
    将模型调用表现数据缓存到磁盘，支持在不同任务间共享。
    数据结构：{cache_key: {model_name: {...metrics...}}}
    """

    _instance: "ModelPerformanceCache | None" = None

    def __init__(self, cache_path: str) -> None:
        self.cache_path = cache_path
        self._lock = RLock()
        self._data: Dict[str, Dict[str, Dict[str, Any]]] = self._load()
        self._last_dump_ts: float = 0.0
        self._dirty: bool = False

    @classmethod
    def instance(cls) -> "ModelPerformanceCache":
        if cls._instance is None:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            cache_dir = os.path.join(project_root, "temp")
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, "model_runtime_stats.json")
            cls._instance = cls(cache_path)
            atexit.register(cls._instance.flush)
        return cls._instance

    def _load(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        if not os.path.exists(self.cache_path):
            return {}
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, dict):
                    # 深拷贝并确保格式正确
                    converted: Dict[str, Dict[str, Dict[str, Any]]] = {}
                    for cache_key, cache_value in data.items():
                        if not isinstance(cache_value, dict):
                            continue
                        converted[cache_key] = {}
                        for model_name, metrics in cache_value.items():
                            if isinstance(metrics, dict):
                                converted[cache_key][model_name] = metrics
                    return converted
        except Exception:
            # 若缓存损坏，忽略并重新开始
            pass
        return {}

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._dirty:
            return
        tmp_path = f"{self.cache_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fp:
                json.dump(self._data, fp, ensure_ascii=False)
            os.replace(tmp_path, self.cache_path)
            self._dirty = False
            self._last_dump_ts = time.time()
        except Exception:
            # 写入失败时忽略，避免影响主流程
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def get_stats(self, cache_key: str) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            stats = self._data.get(cache_key, {})
            return {name: metrics.copy() for name, metrics in stats.items()}

    def _get_or_create_entry(self, cache_key: str, model_name: str) -> Dict[str, Any]:
        stats = self._data.setdefault(cache_key, {})
        entry = stats.get(model_name)
        if entry is None:
            entry = {
                "avg_latency": 0.0,
                "last_latency": 0.0,
                "success_count": 0,
                "fail_count": 0,
                "consecutive_failures": 0,
                "last_call_ts": 0.0,
                "last_success_ts": 0.0,
                "last_fail_ts": 0.0,
                "last_error": "",
            }
            stats[model_name] = entry
        return entry

    def record_success(self, cache_key: str, model_name: str, latency: float) -> None:
        now = time.time()
        with self._lock:
            entry = self._get_or_create_entry(cache_key, model_name)
            entry["last_call_ts"] = now
            entry["last_success_ts"] = now
            entry["last_latency"] = max(latency, 0.0)
            entry["success_count"] = min(entry.get("success_count", 0) + 1, 1_000_000)
            entry["consecutive_failures"] = 0

            prev_avg = entry.get("avg_latency", 0.0)
            if prev_avg <= 0.0:
                entry["avg_latency"] = max(latency, 0.0)
            else:
                alpha = 0.3
                entry["avg_latency"] = prev_avg * (1 - alpha) + max(latency, 0.0) * alpha

            self._mark_dirty_locked()

    def record_failure(self, cache_key: str, model_name: str, latency: float, error_message: str) -> None:
        now = time.time()
        with self._lock:
            entry = self._get_or_create_entry(cache_key, model_name)
            entry["last_call_ts"] = now
            entry["last_fail_ts"] = now
            entry["last_latency"] = max(latency, 0.0)
            entry["fail_count"] = min(entry.get("fail_count", 0) + 1, 1_000_000)
            entry["consecutive_failures"] = min(entry.get("consecutive_failures", 0) + 1, 100)
            entry["last_error"] = error_message[-256:]

            prev_avg = entry.get("avg_latency", 0.0)
            penalty_latency = max(latency, 0.0) if latency > 0 else 10.0
            if prev_avg <= 0.0:
                entry["avg_latency"] = penalty_latency
            else:
                alpha = 0.5
                entry["avg_latency"] = prev_avg * (1 - alpha) + penalty_latency * alpha

            self._mark_dirty_locked()

    def _mark_dirty_locked(self) -> None:
        self._dirty = True
        now = time.time()
        if now - self._last_dump_ts >= 5:
            self._flush_locked()
