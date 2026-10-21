# 第三方组件与素材

项目依赖包括 Electron、electron-builder、TypeScript、esbuild、jsdom、DOMPurify、ECharts/zrender、Python 研究工具等，版本以各依赖清单和锁文件为准。依赖本体不包含在源码包中。

已从本地锁文件、独立验证环境和官方上游生成 [第三方资料清单](docs/third-party/README.md)：397条组件记录、325份去重后的许可证/NOTICE文本。17条记录仍需补证，详见 docs/third-party/FOLLOWUP.json。此前为26条 esbuild 平台组件补充证据：核验官方 npm 发布包的名称、版本、锁文件完整性及仓库声明，关联对应版本标签的上游 LICENSE.md；这些原文已有相同内容哈希，因此该阶段没有增加全文去重数量。来源地址、版本标签、发布包完整性和原文哈希均记录于 inventory.json。这不等于确认原生二进制的全部第三方义务。

后续补证后，现为397条组件、325份去重原文，剩余17条缺口。Python中已核验的发布包版本记录为 artifact_version，不代表已安装；未锁定的akshare、rq及Redis后端约束继续保留。Qlib和Loguru以已核对源码版本的不可变上游提交补充原文，未假称这些文本在所检wheel中存在。isarray保留原发布包内含完整MIT文本的README，记录来源归档与完整性。每份证据均关联URL和原文哈希。

这不是完成的法律合规结论。Python声明范围和安装版本分别列示，不能把验证环境当作完整锁定的产品环境。正式安装包还必须单独核对 Electron/Chromium、Python及其他原生二进制随附的许可证、NOTICE与署名。当前不应删除已有署名或直接将这些组件全部套用未来项目许可证。

当前角色素材移除及旧Git历史边界见 docs/ASSET-RIGHTS.md。第三方素材与商标不自动适用项目MIT许可证。
