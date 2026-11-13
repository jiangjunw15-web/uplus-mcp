"""实验2 知识图谱实践：红楼梦人物关系三元组 + 图查询（等价于 Cypher 的语义）。

用纯 Python 的内存图执行一跳邻居、最短关系路径、二跳扩展与关系类型统计，
输出真实结果，供实验报告引用；Cypher 语句与 Python neo4j driver 代码在报告中给出。
"""
from __future__ import annotations

import json
from collections import Counter, deque

TRIPLES = [
    ("贾母", "子女", "贾赦"), ("贾母", "子女", "贾政"),
    ("贾赦", "配偶", "邢夫人"), ("贾政", "配偶", "王夫人"),
    ("贾赦", "子女", "贾琏"), ("贾赦", "子女", "贾迎春"),
    ("贾政", "子女", "贾宝玉"), ("贾政", "子女", "贾元春"), ("贾政", "子女", "贾探春"),
    ("王夫人", "子女", "贾宝玉"), ("王夫人", "子女", "贾元春"),
    ("贾琏", "配偶", "王熙凤"),
    ("贾敏", "子女", "林黛玉"),
    ("贾母", "子女", "贾敏"),
    ("薛姨妈", "子女", "薛宝钗"), ("薛姨妈", "子女", "薛蟠"),
    ("薛姨妈", "兄弟姐妹", "王夫人"),
    ("贾宝玉", "表亲", "林黛玉"), ("贾宝玉", "表亲", "薛宝钗"),
    ("贾宝玉", "表亲", "史湘云"),
    ("贾宝玉", "主仆", "袭人"), ("贾宝玉", "主仆", "晴雯"),
    ("贾宝玉", "朋友", "妙玉"),
    ("秦可卿", "配偶", "贾蓉"),
    ("贾探春", "兄弟姐妹", "贾宝玉"),
    ("贾迎春", "兄弟姐妹", "贾探春"),
    ("贾惜春", "兄弟姐妹", "贾探春"),
]

PEOPLE = sorted({t[0] for t in TRIPLES} | {t[2] for t in TRIPLES})


def build_graph(triples):
    adj: dict[str, list[tuple[str, str, str]]] = {p: [] for p in PEOPLE}
    for src, rel, dst in triples:
        adj[src].append((dst, rel, "out"))
        adj[dst].append((src, rel, "in"))
    return adj


def one_hop(adj, name):
    return [{"人物": n, "关系": r, "方向": "→" if d == "out" else "←"}
            for n, r, d in adj[name]]


def shortest_path(adj, start, goal):
    prev: dict[str, tuple[str, str] | None] = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        for nxt, rel, d in adj[cur]:
            if nxt in prev:
                continue
            prev[nxt] = (cur, rel)
            q.append(nxt)
    if goal not in prev:
        return []
    steps, node = [], goal
    while prev[node] is not None:
        p, rel = prev[node]
        steps.append({"from": p, "rel": rel, "to": node})
        node = p
    return list(reversed(steps))


def two_hop(adj, name):
    first = {n for n, _, _ in adj[name]}
    second = set()
    for n in first:
        for m, _, _ in adj[n]:
            if m != name:
                second.add(m)
    return {"一跳人数": len(first), "二跳新增人数": len(second - first),
            "二跳总人数": len(first | second),
            "二跳新增人物": sorted(second - first)}


def main() -> None:
    adj = build_graph(TRIPLES)
    rel_counter = Counter(rel for _, rel, _ in TRIPLES)
    out = {
        "triple_count": len(TRIPLES),
        "person_count": len(PEOPLE),
        "people": PEOPLE,
        "q1_baoyu_one_hop": one_hop(adj, "贾宝玉"),
        "q1_count": len(one_hop(adj, "贾宝玉")),
        "q2_path_baoyu_daiyu": shortest_path(adj, "贾宝玉", "林黛玉"),
        "q3_baoyu_two_hop": two_hop(adj, "贾宝玉"),
        "q4_rel_types": dict(sorted(rel_counter.items(), key=lambda kv: -kv[1])),
        "q4_top_person": Counter(
            [n for n, _, _ in adj["贾母"]]
        ).most_common(0),
        "degree": dict(sorted(
            ((p, len(adj[p])) for p in PEOPLE), key=lambda kv: -kv[1])),
    }
    with open("reports/_kg_result.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: out[k] for k in (
        "triple_count", "person_count", "q1_count", "q2_path_baoyu_daiyu",
        "q3_baoyu_two_hop", "q4_rel_types")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
