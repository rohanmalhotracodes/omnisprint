import json
import shutil
import subprocess
from typing import List, Dict, Any, Optional


class CoralClient:
    def __init__(self):
        self.path = shutil.which("coral")

    def available(self) -> bool:
        return self.path is not None

    def run_sql(self, sql: str, timeout: int = 30) -> List[Dict[str, Any]]:
        """Run a Coral SQL query and return JSON rows."""
        if not self.available():
            raise RuntimeError("Coral CLI not available")

        cmd = [self.path, "sql", "--format", "json", sql]
        try:
            proc = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Coral SQL timed out after {timeout}s") from e
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or e.stdout or "").strip()
            raise RuntimeError(f"Coral SQL failed: {detail}") from e

        out = (proc.stdout or "").strip()
        if not out:
            return []
        try:
            data = json.loads(out)
        except Exception as e:
            snippet = out[:300]
            raise RuntimeError(f"Coral SQL returned non-JSON output: {snippet}") from e

        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "rows" in data:
            rows = data["rows"]
            if isinstance(rows, list):
                return rows
        if isinstance(data, dict):
            return [data]
        return []

    def list_sources(self) -> List[str]:
        if not self.available():
            return []
        cmd = [self.path, "source", "list", "--json"]
        try:
            proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out = proc.stdout.strip()
            data = json.loads(out)
            # Expect a list of objects with a name/title field
            names = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        names.append(item.get("name") or item.get("id") or item.get("title"))
            return [n for n in names if n]
        except Exception:
            # Fallback to raw listing
            try:
                proc = subprocess.run([self.path, "source", "list"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                lines = proc.stdout.splitlines()
                names = [l.strip() for l in lines if l.strip()]
                return names
            except Exception:
                return []
