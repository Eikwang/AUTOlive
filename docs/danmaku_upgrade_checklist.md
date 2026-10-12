# DanmakuListener 副本升级 Checklist（F6 沉淀，2026-10-12）

> 来源：v0.7.0 合并（/autoplan run main-20261011-182523）实测发现的 **6 处指南失真**。升级时以本 checklist 核对，**权威来源永远是大胆实测，不是指南文字**。建议：每次上游版本合并后复核本清单并追加新发现；每季度开一次同步窗口对照上游文档修正。

## 一、升级前置判据（缺一即止步）

1. `git merge-base --is-ancestor <基线提交> HEAD`——上游 main 包含基线。
2. `git rev-parse <tag>^{commit}` 与 HEAD 比对——tag 后漂移提交须逐个核对变更范围（**纯文档提交可放行，代码面必须与 tag 等价**）。
3. 上游全量测试复测（上游 CLAUDE.md 约定命令，如 `python -m pytest tests/ -q`）全绿才拷贝。
4. 拷贝后差量断言：以 `git diff --name-status` 为权威，与"新增 N+修改 M"清单比对（含 3 处文档性必现变化：COPY_MANIFEST.json / schema.json / schema.md）。
5. `python -m danmaku_listener contract` 版本号不变（additive-only）。

## 二、六类指南失真与核对方法（v0.7.0 实录）

| # | 失真类型 | v0.7.0 实例 | 核对方法 |
|---|---|---|---|
| 1 | **配置键表漏列** | 指南 14 键表漏 `send_jitter_seconds` / `send_rate_key` / `send_lock_timeout_seconds` / `send_session_idle_timeout_seconds` | 以组件 `config/settings.py` 的 diff 为权威，逐键对指南表 |
| 2 | **依赖声明失真** | 指南称"quickjs/psutil/playwright 均已在主依赖"——AUTOlive requirements 均未声明，仅 runtime312 users.pth 隐式注入 | 以 AUTOlive `requirements*.txt` 实测导入为准，对照上游 `pyproject.toml` |
| 3 | **文件清单漏列** | 指南清单漏 `web/app.py`（以其 §2 头部"36 文件"总数兜底发现） | `git diff --name-status` 全量比对，不信任指南逐文件清单 |
| 4 | **新增/变更表述过时** | `ws_token_file`/`web_port` 被列为"新增"，实为 v0.4.0 副本既有键 | 对照本地副本现状（settings.py 现文件）而非指南"变更说明" |
| 5 | **配置示例误导** | 指南 overrides 示例 `{"huya": 35}` 会覆写内置兜底（虎牙 35s 是内置、本无需配置且下调有风控底线） | 对照组件内置语义（guard.py/huya.py），示例只参考不照抄 |
| 6 | **线格式大小写失真** | 指南冒烟清单写回执 `SENT`，实际线格式小写 `sent`（models.py 枚举值） | 以 `contract/models.py` 枚举值为权威 |

## 三、数据迁移纪律（每轮升级适用）

- cookie/ 与 persistence_data/ **禁止整目录互拷**：cookie 用 `/D` newest-wins 逐文件；persistence_data 仅选择性迁移**发送三件**（send_token.txt / send_audit.jsonl / send_idempotency.json）。
- 迁移前整目录备份至 `backups/pre-v070-migration/`（持久目录，不落工具临时区）。
- **AUTOlive 自有 rooms.json / blocked_keywords.json 永不覆盖**（上游同名文件是其测试注册表）。
- 运行时数据只落仓库根（副本目录会被 copy_danmaku.py rmtree）。

## 四、升级后验证（顺序执行）

1. 契约版本一致 → 2. `python -X utf8 tests/smoke_danmaku.py` 全 PASS（含无 8765 断言）→ 3. 相关单测（tests/test_danmaku_consumer.py 等）→ 4. 主系统启动冒烟（web_console 已启动日志 + 8086 与主 webui 同 PID）→ 5. 人工冒烟项（dry_run→实发→重登按钮）。

## 五、维护约定

- 新失真实录追加到第二节表格（一行一例，注明版本）。
- 每季度同步窗口：对照上游 docs/ 更新本清单；上游指南若已修正对应条目，标注"上游已修"。
