class FakeZotero:
    def __init__(self, library_id, library_type, api_key, **kw):
        self._colls: dict[str, dict] = {}
        self._items: dict[str, dict] = {}
        self._item_order: list[str] = []
        self._seq = 0
        # （PDF 附件能力已移除，本机 connector 相关字段仅保留最小占位）
        self.library_id = library_id
        self.library_type = library_type
        self.endpoint = "http://localhost:23119/api"

    def _k(self, prefix: str) -> str:
        # 真实 Zotero key 为 8 位 base62（含字母）；此处用 hex 生成（0-9A-F 是
        # base62 子集，仍会被 client/manager 的 base62 判定识别为 key）
        self._seq += 1
        return f"{prefix}{self._seq:07X}"

    # ── read ──
    def everything(self, query):
        return list(query)

    def collections(self, **kw):
        return [
            {"data": {"key": k, "name": v["name"], "parentCollection": v["parent"]}}
            for k, v in self._colls.items()
        ]

    def items(self, tag=None, limit=None, start=0):
        out = []
        for k in self._item_order:
            d = self._items[k]["data"]
            if tag and not any(
                (t.get("tag") if isinstance(t, dict) else t) == tag for t in (d.get("tags") or [])
            ):
                continue
            out.append(self._items[k])
        return out[start : start + limit] if limit else out[start:]

    # ── write ──
    def create_collections(self, payload):
        successful, failed = {}, {}
        for i, item in enumerate(payload):
            key = self._k("C")
            self._colls[key] = {"name": item["name"], "parent": item.get("parentCollection", "")}
            successful[str(i)] = {"key": key}
        return {"successful": successful, "failed": failed}

    def create_items(self, payload, parentid=None):
        successful, failed = {}, {}
        for i, item in enumerate(payload):
            key = self._k("I")
            data = dict(item)
            data["key"] = key
            data["version"] = 1
            if parentid:
                data["parentItem"] = parentid
            self._items[key] = {"key": key, "version": 1, "data": data}
            self._item_order.append(key)
            successful[str(i)] = {"key": key}
        return {"successful": successful, "failed": failed}
