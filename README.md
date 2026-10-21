# A-pi 独立开源准备副本

这是从本地工作台按源码白名单导出的待审核副本，不是已经公开发布的开源版本。未复制 Git 历史、账号库、密钥、证书私钥、行情库、研究记录、模型产物、依赖目录或安装包。原工作台不部署、不重启。

## 目录

- outputs/a_share_quant：Python 后端、研究与回测代码、测试、桌面壳源码。
- outputs/a_pi_site：网页源码（dist 是源码目录）、构建工具和测试。
- docs：授权声明、已知限制和发布检查清单。

## 开始开发

请在此副本创建全新虚拟环境，不复用生产环境的 .env、账号、证书或数据目录。安装后端依赖：进入 outputs/a_share_quant，执行 python -m venv .venv，激活后执行 python -m pip install -e ".[dev,ml]"，以及 python -m pip install -r requirements-tushare.txt。

前端进入 outputs/a_pi_site，执行 npm ci，再执行 npm run build、npm run typecheck、npm run check:syntax、npm test。后端可先使用 python -m pytest；部分集成测试依赖额外环境，不应据此声称全部测试通过。

本副本不提供可直接连接生产账户的启动器。服务要求本机 HTTPS，开发者须自行生成仅用于本副本的 localhost 证书并通过 --tls-cert、--tls-key 指定。建议使用独立端口 18000、独立 A_PI_DATA_ROOT，并设置 A_PI_PAPER_AUTO=0、A_PI_DEEP_AUTO=0，传入 --no-paper-scheduler。不要复制生产证书或私钥。账号通过 scripts/user_admin.py 的交互式 adduser 流程在独立数据目录新建，不提供默认密码。

行情与模型服务均须使用开发者自己的合法权限和配置；没有数据时不提供合成数据冒充真实结果。研究预算、PIT、未来标签成熟与密封验收门禁必须保留。

## 发布状态

2026-10-20 的独立副本修复与验证记录见 docs/REPAIR_VALIDATION_20261020.md。通过模拟测试不代表通过正式因子验收或生产部署。

公开发布后续进度见 [发布准备记录](docs/PUBLIC_RELEASE_PROGRESS_20261020.md)，包含许可证资料缺口、安全复核和干净Windows验收边界。

后续发现并修复的优化模式校验问题见 [数据验证加固](docs/VALIDATION_HARDENING_20261020.md)。原生崩溃对照见 [运行时诊断记录](docs/RUNTIME_DIAGNOSTICS_20261020.md)：官方Python对照通过不代表捆绑解释器根因已修复，也不是建议生产使用旧安全补丁版本。

独立 CPU 深度训练环境要求 Python 3.12。先用你自己安装的Python 3.12在 outputs/a_share_quant 下创建 deep_factor/.venv，再用该环境解释器执行 deep_factor/install_runtime.py。调度器默认从该项目内虚拟环境读取解释器；也可通过 A_PI_DEEP_PYTHON 指定其他独立Python 3.12解释器的绝对路径，不再默认查找Codex缓存。运行依赖安装在本副本 deep_factor/.vendor，禁止提交这两个依赖目录。Windows 测试宜使用短临时路径，避免 SQLite WAL 文件超出路径限制。LLM 竞技参考资料现作为 quant 包资源分发，安装 wheel 后也可读取。

项目原创代码采用 MIT，见 LICENSE；第三方组件按各自许可证授权。用户已要求从独立发布副本移除库洛米素材，当前使用新建的几何 SVG 标记。仓库仍保持私有，依赖资料、贡献来源与安装验收未完成前不要宣称已完成公开发布。仅供技术研究，不构成投资建议；不保证盈利，也不代表通过生产安全审计。
