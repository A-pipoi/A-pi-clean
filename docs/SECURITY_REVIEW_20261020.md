# 发布前安全复核记录

日期：2026-10-20。范围仅为独立源码副本。没有读取生产凭据、关闭门禁或进行生产攻击测试。

续轮最新结论见SECURITY_FOLLOWUP_20261020.md：已加固数据扩展请求的地址/重定向/响应读取，补修验收脚本优化模式与归档标识符；最新Bandit为0高、23中、45低，下文保留前几轮历史记录。Git历史自动扫描已覆盖基点全部7个提交，边界见GIT_HISTORY_REVIEW_20261020.md；人工审计及来源证明仍未结案。许可证资料仍17条缺口。

## 已做与未做

- 已完成源码白名单、选定原文件哈希核对、私钥/常见凭据特征扫描、依赖已知漏洞扫描及认证/CSRF/跨账户等模拟测试。
- Bandit新源码检查保留24项中等、84项低等级提示；不通过删除规则或加入通用忽略把它们清零。
- 这些工作不是完整渗透测试、全部调用链审计或“零秘密/零漏洞”保证；剩余84项低等级提示尚未逐条完成人工分流。

## 本轮重点判读

| 范围 | 已见证据 | 结论与后续 |
|---|---|---|
| Qlib源表拼接 | prepare_qlib_data.unique_rows仅接受ts_daily/ts_adj_factor，值用占位符 | 此处未见任意外部表名直接进入SQL；不推广为整个项目无注入 |
| 财务观测表 | observed_rows对fina_audit/forecast/express做固定集合检查 | 此处有明确表名白名单及日期参数绑定 |
| analytics.latest_rows | endpoint先检查ENDPOINTS，period使用占位符 | 保留静态提示，记录现有防护 |
| extension_derived | 表名来自函数内固定元组，scope来自固定分支，参数绑定 | 此处没有把请求字符串作为SQL片段使用 |
| 其他SQL提示 | 涉及用户字段更新、ledger更新列、脚本表名和占位符等 | 尚需逐个核对外部输入到最终SQL的完整链路；未统一判成误报 |
| 行情传输地址 | 默认HTTPS；环境变量A_PI_TUSHARE_API_URL可覆盖，当前仅检查https前缀，urllib默认处理重定向 | 自定义主机、userinfo、端口、代理、重定向及凭据目的地主机政策尚未完成安全验收；不把前缀检查当完整SSRF/凭据外发防护 |
| WebSocket握手 | SHA-1用于协议握手，并标注usedforsecurity=False | 不用于密码/签名；不应换算法破坏协议 |
| 密封/未来标签门禁 | 模拟测试覆盖拒绝复用、跨账户及未成熟数据等路径 | 模拟通过不代表获得真实数据证据或通过正式因子验收 |

在明确支持的自定义行情/代理地址政策前，不擅自改成任意放行或删除既有功能；部署者不应把未经审核的目的地址与真实Token组合使用。本轮未调用真实行情。

## 公开前必须确认

许可证资料缺口现降至19条，仍需对原生安装组件及公开源码的贡献来源继续补证。若发现真实密钥，应停止分发并由持有人轮换，不能仅删当前文件后继续公开。用户随后选择MIT并要求从当前发布快照移除角色素材；旧Git历史尚未清理，仓库仍保持私有。

## 后续复核与修复

已定位并修复两份Qlib数据校验脚本依赖assert的优化模式失效问题，见VALIDATION_HARDENING_20261020.md；其余断言仍需按调用链复核。新源码Bandit结果为0高、24中、84低，不通过删规则清零；97低是修复前的历史结果。新增20项专门回归、Ruff/Mypy通过，非完整渗透结论。

补查auth_store._update只有两处固定SQL片段调用；ledger.update_backtest_job列名来自固定allowed集合、值参数绑定；auth_migration_audit数据库表名使用双引号转义并只读连接。上述局部证据不推广为整个项目无注入。

## 本轮续查

运行时探针的4项assert已改为显式ValueError，并补充空标签/空特征拒绝；22项默认及优化模式隔离模拟通过。异常上游字段先验类型再set去重，5项回归确认嵌套对象不会触发原始TypeError。新源码扫描为0高、24中、80低，未禁用规则、未用nosec压制。80低仍不等于80个已确认漏洞，也不能宣称均为误报。

补查extension_features表名来自模块内SPECS；historical_experiments表名来自fund_daily/fund_adj固定二元组；extension_store.report来自ENDPOINTS；forward_samples及audit_forward_production来自membership/samples/labels固定元组；execution.store清表与存在性检查来自固定表名；ledger.payload_columns来自include_bars布尔分支；preflight_ruff_deployment表名来自固定元组。普通数据值均按所见调用参数绑定。archive_approved_baseline_cycle的列名来自数据库PRAGMA并直接拼接，仍依赖受信任数据库模式，未经恶意模式测试；不执行生产归档。

HTTP claims断言位于鉴权分支之后，schema.signal_*断言位于非空/有限值校验之后，extension_derived.close_row断言前有对应close缺失拒绝；这些局部断言移除不等于移除了前置门禁。计划/外部服务验收脚本中的assert仍可能在-O下失去验证作用，因此这些脚本暂不作为优化模式验收证据。未调用真实模型或行情，也未消耗研究预算。
