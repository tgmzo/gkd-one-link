# 规则来源与许可

本项目是独立应用规则的汇编，各应用和全局组的原始规则内容保留不变，来源映射见 `dist/merge-report.json`。合并器与工作流于 2026-11-12 由本仓库维护者创建，以 GPL-3.0-only 提供；`LICENSE` 是其完整文本。各上游规则仍保留以下各自的许可与署名，不能把整个规则汇编中的 Lin-arm 部分声称为 GPL 或 MIT。

| 来源 | 作者/维护者 | 许可依据 |
| --- | --- | --- |
| [Lin-arm](https://github.com/Lin-arm/GKD_subscription) | Lin-arm 及原仓库贡献者 | 依据维护者提供的项目授权，原文和提供日期见 [授权记录](licenses/Lin-arm-permission.md)。未声称独立核验或上游采用标准开源许可。 |
| [奥怪](https://github.com/aoguai/subscription/tree/custom) | aoguai 及上游贡献者 | GPL-3.0-only；保留 [GPL](LICENSE) 和 [LEGAL.md](licenses/aoguai-LEGAL.md)。 |
| [甘霖](https://github.com/ganlinte/GKD-subscription) | ganlinte | [MIT，Copyright (c) 2024 ganlinte](licenses/ganlinte-MIT.txt)。 |
| [梦念逍遥](https://github.com/MengNianxiaoyao/gkd-subscription) | 梦念逍遥 | 上游 [package.json 的 MIT 声明](licenses/MengNianxiaoyao-package.json) 与 [MIT 文本](licenses/MengNianxiaoyao-MIT.txt)。 |

每次合并按上游 commit 下载，不修改所选应用内的规则。变换仅包括：选择每个应用的优先来源、选择主源全局组、合并分类、替换订阅身份与更新地址。具体程序与报告构成可审查的变换说明。

`dist/upstream/` 保存本次下载的 JSON5 快照；`ao-source.tar.gz` 保存奥怪对应 commit 的完整源代码、构建工具和依赖清单，供按 GPL 获取对应源码。原仓库链接、commit、哈希和逐应用来源见合并报告。GPL 规则与本项目变换代码可依 GPL-3.0-only 修改、再分发；MIT 部分保留其完整许可。Lin-arm 授权不是给所有下游重新许可的标准开源协议。

所有规则和程序均不附带担保。规则的实际效果取决于 Android、应用版本和 GKD 的权限。

