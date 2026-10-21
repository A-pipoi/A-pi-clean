# A-pi · A 股量化研究工作台

基于 Python、JavaScript 和 Electron 的本地量化研究工作台，提供因子研究、策略回测、风险分析与纸面交易功能。原创代码采用 [MIT 许可证](LICENSE)。

项目源码托管于 [A-pipoi/A-pi-clean](https://github.com/A-pipoi/A-pi-clean)。运行前请配置本地环境、行情数据和所需的模型服务。账号、API 密钥、HTTPS 证书及个人研究数据由使用者自行管理。

## 功能

- 行情与研究：接入 Tushare 数据，进行因子挖掘、多模型候选研究和前向 IC 跟踪。
- 回测与风险：策略回测、敏感性检验，以及带成本口径的研究结果展示。
- 纸面交易：独立研究账户、模拟委托、成交与持仓状态管理。
- 研究约束：保留数据可得时间（PIT）、研究预算、版本冻结、未来标签成熟和密封验收检查。
- 本地界面：网页界面与 Electron 桌面壳，通过本机 HTTPS 连接后端。

功能可用性取决于数据权限、依赖和配置；源码提供相关实现，不附带已通过正式验收的策略或模型。

## 目录

- outputs/a_share_quant：Python 后端、研究与回测代码、测试、桌面壳源码。
- outputs/a_pi_site：网页源码（dist 是源码目录）、构建工具和测试。
- docs：授权声明、已知限制和发布检查清单。

## 环境准备

后端需要 Python 3.11 或更高版本；可选的 CPU 深度训练环境使用 Python 3.12。前端构建需要 Node.js 和 npm。行情功能需要具备相应接口权限的 Tushare 账号，LLM 功能需要配置对应模型服务。

以下命令以 Windows PowerShell 为例，各节均从仓库根目录开始执行。

### 后端依赖

```powershell
cd outputs/a_share_quant
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ml]"
.\.venv\Scripts\python.exe -m pip install -r requirements-tushare.txt
```

### 前端构建

```powershell
cd outputs/a_pi_site
npm ci
npm run build
```

### 本地配置

使用独立的虚拟环境和数据目录，并参考 [配置示例](.env.example) 设置运行参数。

- **HTTPS**：为本机生成 localhost 证书，启动服务时通过 `--tls-cert` 和 `--tls-key` 指定证书及私钥。
- **数据目录**：通过 `A_PI_DATA_ROOT` 设置数据保存位置。开发环境建议使用端口 `18000`。
- **自动任务**：首次配置时建议设置 `A_PI_PAPER_AUTO=0`、`A_PI_DEEP_AUTO=0`，并使用 `--no-paper-scheduler` 关闭纸面交易调度；确认配置后再按需开启。
- **账号**：通过 `scripts/user_admin.py` 的交互式 `adduser` 流程创建本地账号，项目没有默认密码。
- **数据与模型**：配置自己的 Tushare 和模型服务凭据。缺少数据或权限时，相关功能会提示不可用。

### 深度训练环境（可选）

在后端项目中创建独立的 Python 3.12 环境，并安装训练依赖：

```powershell
cd outputs/a_share_quant
py -3.12 -m venv deep_factor/.venv
.\deep_factor\.venv\Scripts\python.exe deep_factor/install_runtime.py
```

训练调度器默认使用 `deep_factor/.venv` 中的解释器，也可通过 `A_PI_DEEP_PYTHON` 指定其他 Python 3.12 解释器的绝对路径。训练依赖保存在 `deep_factor/.vendor`，虚拟环境和依赖目录均无需提交到版本库。

## 开发检查

前端检查：

```powershell
cd outputs/a_pi_site
npm run typecheck
npm run check:syntax
npm test
```

后端测试：

```powershell
cd outputs/a_share_quant
.\.venv\Scripts\python.exe -m pytest
```

部分集成测试需要额外的数据、服务或依赖。测试结果应结合实际运行环境判断；Windows 下建议使用较短的项目和临时目录路径。

## 已知限制

当前发布以源码为主，尚未完成干净 Windows 环境的安装验收。已知问题及运行限制见 [KNOWN_ISSUES](docs/KNOWN_ISSUES.md)，安装核对步骤见 [安装验收说明](docs/CLEAN_WINDOWS_ACCEPTANCE.md)。

因子与策略的研究结果受数据覆盖、成本假设和样本区间影响。前向标签需要等待真实行情成熟；历史回放、单元测试和回测结果不能替代正式验收。

## 贡献与许可

欢迎提交问题和改进建议。参与开发请阅读 [贡献指南](CONTRIBUTING.md)，报告安全问题请参考 [安全说明](SECURITY.md)。原创代码采用 [MIT 许可证](LICENSE)，第三方组件的许可信息见 [第三方声明](THIRD_PARTY_NOTICES.md)。

本项目用于量化研究与技术交流，不构成投资建议。
