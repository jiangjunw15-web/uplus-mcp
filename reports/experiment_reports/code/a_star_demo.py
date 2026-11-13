"""实验1 搜索策略实践：A* 栅格路径规划，欧式/曼哈顿启发式与 8/4 邻域对比。

输出真实可复现的运行数据（扩展节点数、Open 表峰值、路径代价、耗时），
并把前若干次扩展记录成 Open/Close 表片段，供实验报告引用。
"""
from __future__ import annotations

import heapq
import json
import random
import time
from typing import Callable, Dict, List, Tuple

W, H = 10, 10
START, GOAL = (0, 0), (W - 1, H - 1)
BLOCK_RATIO = 0.20
SEED = 20261113


def build_grid(w: int, h: int, ratio: float, seed: int) -> List[List[int]]:
    rng = random.Random(seed)
    grid = [[0] * w for _ in range(h)]
    total = w * h
    blocked = int(total * ratio)
    placed = 0
    while placed < blocked:
        r, c = rng.randrange(h), rng.randrange(w)
        if (r, c) in (START, GOAL) or grid[r][c] == 1:
            continue
        grid[r][c] = 1
        placed += 1
    return grid


def neighbors_8(r: int, c: int, h: int, w: int):
    for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1),
                   (0, 1), (1, -1), (1, 0), (1, 1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < h and 0 <= nc < w:
            yield nr, nc, (1.41421356 if dr and dc else 1.0)


def neighbors_4(r: int, c: int, h: int, w: int):
    for dr, dc in ((-1, 0), (0, -1), (0, 1), (1, 0)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < h and 0 <= nc < w:
            yield nr, nc, 1.0


def euclid(goal: Tuple[int, int]) -> Callable[[int, int], float]:
    return lambda r, c: ((r - goal[0]) ** 2 + (c - goal[1]) ** 2) ** 0.5


def manhattan(goal: Tuple[int, int]) -> Callable[[int, int], float]:
    return lambda r, c: abs(r - goal[0]) + abs(c - goal[1])


def a_star(grid, start, goal, hfun, nbrs):
    rows, cols = len(grid), len(grid[0])
    g = {start: 0.0}
    parent: Dict[Tuple[int, int], Tuple[int, int]] = {}
    open_heap = [(hfun(*start), 0.0, start)]
    in_open = {start}
    closed = set()
    trace: List[dict] = []
    peak = 1
    expanded = 0
    t0 = time.perf_counter()
    while open_heap:
        f, cur_g, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        in_open.discard(cur)
        closed.add(cur)
        expanded += 1
        if len(trace) < 12:
            trace.append({"step": expanded, "node": cur, "g": round(cur_g, 3),
                          "h": round(hfun(*cur), 3), "f": round(f, 3),
                          "open": len(in_open), "closed": len(closed)})
        if cur == goal:
            break
        r, c = cur
        for nr, nc, cost in nbrs(r, c, rows, cols):
            if grid[nr][nc] == 1:
                continue
            ng = cur_g + cost
            if ng < g.get((nr, nc), float("inf")):
                g[(nr, nc)] = ng
                parent[(nr, nc)] = cur
                heapq.heappush(open_heap, (ng + hfun(nr, nc), ng, (nr, nc)))
                in_open.add((nr, nc))
        peak = max(peak, len(in_open))
    elapsed = (time.perf_counter() - t0) * 1000
    path: List[Tuple[int, int]] = []
    if goal in closed:
        node = goal
        while node != start:
            path.append(node)
            node = parent[node]
        path.append(start)
        path.reverse()
    return {"found": bool(path), "expanded": expanded, "open_peak": peak,
            "path_len": len(path), "cost": round(g.get(goal, float("inf")), 3),
            "ms": round(elapsed, 2), "trace": trace, "path": path}


def render(grid, path, start, goal):
    cells = []
    for r in range(H):
        row = ""
        for c in range(W):
            if (r, c) == start:
                row += "S"
            elif (r, c) == goal:
                row += "G"
            elif (r, c) in path:
                row += "*"
            elif grid[r][c] == 1:
                row += "#"
            else:
                row += "."
        cells.append(row)
    return cells


def main() -> None:
    grid = build_grid(W, H, BLOCK_RATIO, SEED)
    cases = [
        ("欧式启发+8邻域", euclid(GOAL), neighbors_8),
        ("曼哈顿启发+8邻域", manhattan(GOAL), neighbors_8),
        ("欧式启发+4邻域", euclid(GOAL), neighbors_4),
        ("曼哈顿启发+4邻域", manhattan(GOAL), neighbors_4),
    ]
    out = {"grid": ["".join("#" if v else "." for v in row) for row in grid],
           "params": {"W": W, "H": H, "block_ratio": BLOCK_RATIO, "seed": SEED},
           "cases": {}}
    for name, hf, nbr in cases:
        res = a_star(grid, START, GOAL, hf, nbr)
        out["cases"][name] = {
            "found": res["found"], "expanded": res["expanded"],
            "open_peak": res["open_peak"], "path_len": res["path_len"],
            "cost": res["cost"], "ms": res["ms"],
            "trace": res["trace"], "path": res["path"],
            "blocked_cells": sum(sum(row) for row in grid),
        }
    out["map_best"] = render(grid, out["cases"]["欧式启发+8邻域"]["path"], START, GOAL)
    out["map_manhattan8"] = render(grid, out["cases"]["曼哈顿启发+8邻域"]["path"], START, GOAL)
    with open("reports/_astar_result.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: {kk: vv for kk, vv in v.items()
                         if kk in ("found", "expanded", "open_peak", "path_len", "cost", "ms")}
                      for k, v in out["cases"].items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
