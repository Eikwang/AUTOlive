# -*- coding: utf-8 -*-
"""fastrag python 路线 10 问延迟实测（Eng F-E11/F-4 终门判据，2026-10-15）
判据：python p95 ≤ rust p95×2 则选 python（终门 uc1a 已选 python，本实测为判据确认）。
前置：fastrag 服务已启动（本地 HTTP 端点）；结果写入 logs/fastrag_latency_python.json。
用法：python Scripts/bench_fastrag_latency.py
"""
import json
import statistics
import time
import urllib.request

QUESTIONS = [
    "公司的主营业务是什么？",
    "去污液的使用方法是什么？",
    "大桶洗洁精多少钱一桶？",
    "公司的联系方式是什么？",
    "你们的产品有哪些规格？",
    "批发价格怎么算？",
    "发货周期是多久？",
    "产品有什么售后服务？",
    "公司的地址在哪里？",
    "大盘卷纸的规格参数是什么？",
]

URL = "http://127.0.0.1:11420/query"


def ask(question: str) -> tuple[float, str]:
    body = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    dt = time.perf_counter() - t0
    answer = (data.get("answer") or "")[:40]
    return dt, answer


def main() -> None:
    # 预热 1 问（模型首次推理含加载开销，不计入统计）
    dt0, _ = ask(QUESTIONS[0])
    print(f"预热 1 问: {dt0:.2f}s（不计入统计）")
    times = []
    for q in QUESTIONS:
        dt, ans = ask(q)
        times.append(dt)
        print(f"{dt:6.2f}s | {q} -> {ans}")
    times.sort()
    p50 = statistics.median(times)
    p95 = times[int(len(times) * 0.95) - 1] if len(times) > 1 else times[0]
    result = {
        "route": "python (fastrag-python server.py, runtime312)",
        "n": len(times),
        "mean_s": round(statistics.mean(times), 2),
        "p50_s": round(p50, 2),
        "p95_s": round(p95, 2),
        "min_s": round(min(times), 2),
        "max_s": round(max(times), 2),
        "date": "2026-10-15",
    }
    print(json.dumps(result, ensure_ascii=False, indent=1))
    with open(r"D:\AI\AUTOlive\logs\fastrag_latency_python.json", "w", encoding="utf-8") as f:
        json.dump({"per_query": [round(t, 2) for t in times], **result}, f, ensure_ascii=False, indent=1)
    print("结果已写入 logs/fastrag_latency_python.json")


if __name__ == "__main__":
    main()
