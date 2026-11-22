"""API 文档与代码的一致性护栏。

接口改了但文档没改，是最常见的「文档腐烂」。这里用可执行的方式盯住两件事：
1. `docs/API.md` 覆盖了服务端所有 `/api` 路径；
2. `docs/openapi.json` 与 FastAPI 实际路由一致（重新生成后 diff 为空）。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "API.md"
SPEC = ROOT / "docs" / "openapi.json"


def _load_app():
    os.environ.setdefault(
        "DINGFENGBO_SERVER_DB", str(Path(tempfile.mkdtemp(prefix="dfb-apidoc-")) / "cloud.db")
    )
    import server.main as server  # noqa: PLC0415

    return server.app


class ApiRouteCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _load_app()
        cls.routes = {
            (method, route.path)
            for route in cls.app.routes
            if getattr(route, "path", "").startswith("/api")
            for method in (route.methods or ())
            if method not in ("HEAD", "OPTIONS")
        }

    def test_every_route_is_documented(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        missing = [f"{method} {path}" for method, path in sorted(self.routes) if path not in text]
        self.assertEqual(missing, [], f"以下接口没有写进 docs/API.md：{missing}")

    def test_doc_mentions_sync_tables(self) -> None:
        from app.db.models import MODEL_BY_TABLE

        text = DOC.read_text(encoding="utf-8")
        missing = [name for name in MODEL_BY_TABLE if f"`{name}`" not in text]
        self.assertEqual(missing, [], f"以下同步表没在文档里列出：{missing}")


class OpenApiSnapshotTests(unittest.TestCase):
    def test_snapshot_matches_routes(self) -> None:
        self.assertTrue(SPEC.exists(), "docs/openapi.json 缺失，先跑 python scripts/export_openapi.py")
        snapshot = json.loads(SPEC.read_text(encoding="utf-8"))
        app = _load_app()
        live = app.openapi()
        self.assertEqual(
            sorted(snapshot["paths"]), sorted(live["paths"]),
            "openapi.json 与实际路由不一致，请重新运行 scripts/export_openapi.py",
        )
        for path, operations in live["paths"].items():
            self.assertEqual(
                sorted(snapshot["paths"][path]), sorted(operations),
                f"{path} 的方法集变了，请重新生成 openapi.json",
            )


if __name__ == "__main__":
    unittest.main()
