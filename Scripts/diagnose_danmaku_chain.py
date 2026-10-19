# -*- coding: utf-8 -*-
"""弹幕承接链路静态诊断（uc1 交付物，/autoplan 批准批次切片 2）。

用法：python Scripts/diagnose_danmaku_chain.py
逐环节打印配置现值与判定，定位"弹幕被分发但无回复"的症状断点。
运行时复现（listen --replay demo.jsonl）需启动系统后人工执行——本脚本为静态前置。

候选断点（CEO 审查注记 + Eng E6 + 本批静态核验新增）：
  C1 danmaku_listener.enabled（进程内托管构建条件）
  C2 平台登录态（cookie/登录文件）
  C3 过滤链静默丢弃：违禁词表误伤 / 触发前后缀 / 语言过滤 need_lang / 纯符号
  C4 攒批定时器配置（forget_duration/reserve_num）
  C5 节写者矩阵：webui 直写被 main 覆盖导致配置意图≠运行态
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, WARN, FAIL = "[OK]  ", "[WARN]", "[FAIL]"


def main():
    cfg = json.load(open("config.json", encoding="utf-8"))
    f = cfg.get("filter", {})
    findings = []

    print("=" * 64)
    print("弹幕承接链路静态诊断（uc1）")
    print("=" * 64)

    # C1 托管构建条件
    dl = cfg.get("danmaku_listener", {})
    enabled = dl.get("enabled")
    print(f"\n[C1] danmaku_listener.enabled = {enabled!r}")
    if enabled is not True:
        findings.append("danmaku_listener.enabled 非 true：进程内消费者不构建，全链断（danmaku_consumer.py:1104-1116）")
        print(FAIL + "消费者不会构建")
    else:
        print(OK + "托管构建条件满足")

    # C2 登录态
    print(f"\n[C2] 平台登录态（静态只能查文件存在性）")
    for p in ("cookie.json", "data/cookie", "data/login"):
        if os.path.exists(p):
            print(OK + f"发现登录载体: {p}")
            break
    else:
        print(WARN + "未发现常见登录载体文件（运行期以 webui 登录状态为准）")

    # C3 过滤链静默丢弃点
    print(f"\n[C3] 过滤链静默丢弃点（comment_handle 链）")
    # 触发前后缀
    for k in ("trigger_prefix", "trigger_suffix", "弹幕触发前缀", "弹幕触发后缀"):
        v = f.get(k)
        if v:
            print(f"       {k} = {v!r}（非空：无该前/后缀的弹幕被丢弃）")
    # 违禁词路径
    for k in ("prohibited_words_path", "违禁词路径"):
        p = f.get(k)
        if p:
            exists = os.path.exists(p)
            print((WARN if exists else FAIL) + f"违禁词路径 {p} 存在={exists}")
            if exists:
                try:
                    content = open(p, "rb").read()
                    try:
                        content.decode("utf-8")
                        lines = [l for l in content.decode("utf-8-sig").splitlines() if l.strip()]
                        print(OK + f"违禁词 {len(lines)} 行（UTF-8 可解码）")
                        common = {"你好", "hello", "666", "哈哈哈", "的主播"}
                        hit = common & set(lines)
                        if hit:
                            findings.append(f"违禁词表含常见词 {hit}——这些弹幕会被静默丢弃（prohibitions_handle 丢弃式）")
                            print(FAIL + f"误伤常见词: {hit}")
                    except UnicodeDecodeError:
                        findings.append(f"违禁词文件 {p} 非 UTF-8——E4 修复前静默空表/乱码，过滤失效或全丢弃")
                        print(FAIL + "非 UTF-8 编码")
                except OSError as e:
                    print(FAIL + f"不可读: {e}")
    # 语言过滤
    need_lang = cfg.get("need_lang", "none")
    print((OK if need_lang in (None, "none", "") else WARN) + f"need_lang = {need_lang!r}" + ("（全部处理）" if need_lang in (None, "none", "") else f"（仅 {need_lang} 弹幕会进入回复链，其余静默丢弃）"))
    # 纯符号/表情过滤开关
    for k in ("comment_check_and_replace", "表情过滤"):
        if k in f:
            print(f"       {k} = {f[k]!r}")

    # C4 攒批定时器
    print(f"\n[C4] 攒批定时器（filter forget 配置）")
    for t in ("comment", "gift", "entrance", "follow"):
        dur, res = f.get(f"{t}_forget_duration"), f.get(f"{t}_forget_reserve_num")
        status = OK if (isinstance(dur, (int, float)) and dur > 0 and isinstance(res, int) and res > 0) else FAIL
        print(f"{status} {t}: duration={dur!r} reserve={res!r}" +
              ("" if status == OK else "——缺失/0 会导致攒批定时器异常或数据不进处理链"))

    # C5 写者矩阵提示
    print(f"\n[C5] 节写者矩阵")
    print(WARN + "webui 直写的 config 修改在 main 进程可达时被覆盖——如 webui 改过 filter 配置但"
          " main 启动早于修改，运行态仍是旧值。核对：重启 main 后再看本脚本输出。")

    print("\n" + "=" * 64)
    if findings:
        print(f"静态发现 {len(findings)} 项候选断点：")
        for i, x in enumerate(findings, 1):
            print(f"  {i}. {x}")
        print("\n下一步：修复上方 FAIL 项后，用 listen --replay demo.jsonl 实测复现（运行时验证）。")
    else:
        print("静态配置未发现明显断点——需运行时复现（listen --replay demo.jsonl + 观察日志）定位。")
        print("重点观察：comment_handle 入口 logger.info（'[{username}]: {content}'）是否打印——")
        print("  打印=分发与攒批正常，断点在其后的过滤/回复链；不打印=断点在监听/分发/攒批配置。")


if __name__ == "__main__":
    main()
