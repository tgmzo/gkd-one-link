# GKD 一个订阅链接

四源按 `Lin-arm → 奥怪 → 甘霖 → 梦念逍遥` 的顺序合并，每天自动检查更新。

订阅地址：

```text
https://raw.githubusercontent.com/tgmzo/gkd-one-link/main/dist/gkd.json5
```

将此地址添加到 GKD 的订阅管理。订阅 ID 固定为 `199427417`。主源已有的应用完整保留，其他源只补充没有覆盖的应用；同一应用不会叠加多源规则。使用此合并订阅时，同时启用原订阅仍可能重复执行。

## 合并策略

- 按应用包名选择第一份完整规则，保留组编号、规则编号、前置依赖、共享冷却引用和原有启用设置。
- 只保留 Lin-arm 的全局规则组，避免多个源的通用点击器同时工作。
- 分类按名称保留主源设置，给新增分类分配唯一编号。GKD 按规则组名称前缀关联分类，原始组名保持不变。
- 输出严格 JSON（也是合法 JSON5），校验必要字段、编号、重复包名和规则结构，再由 GKD 官方工具和选择器编译器验证。上游可被 GKD 容忍的悬空引用记录到报告。
- 版本号仅在实际订阅内容改变时增加；自己的版本检查文件为 `dist/gkd.version.json5`，不沿用上游订阅身份或更新地址。
- 四源全部下载、解析、校验及源码归档成功后才生成输出。任何源失败都会使工作流失败，GitHub 上的已有版本保持可用。

## 自动更新

[Update GKD subscription](https://github.com/tgmzo/gkd-one-link/actions/workflows/update.yml) 每天 **19:23 UTC / 北京时间次日 03:23** 执行，也支持手动运行。程序、配置或测试发生变化会触发首次及后续检查。GitHub 的定时任务可能延迟，公开仓库长期无活动时也可能被 GitHub 暂停；可在 Actions 页面重新启用。

工作流先执行离线回归测试，再按固定 commit 下载四源，合并与官方结构/选择器检查通过后提交完整 `dist/`。并发更新按顺序执行，推送冲突会安全失败，不强制覆盖远端。

`dist/merge-report.json` 包含来源 commit、下载哈希、各源选中应用数、逐应用来源、全局来源与校验警告。`dist/upstream/` 包含对应原始规则快照及 GPL 来源的完整源码归档。

## 来源与授权

详见 [NOTICE.md](NOTICE.md) 和 [licenses/](licenses/)。Lin-arm 部分依据维护者提供的作者授权使用，记录保留原文；没有将其宣称为 GPL/MIT。奥怪部分按 GPL-3.0-only 提供，甘霖和梦念逍遥部分保留 MIT 声明。

本项目于 2026-11-12 创建，合并程序和工作流按 GPL-3.0-only 提供。结构检查无法替代手机上实际点击验证。

## 本地维护

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
npm ci --ignore-scripts
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/merge.py --config sources.json --output-dir dist
node scripts/validate.mjs dist/gkd.json5
```

上游列表为 `sources.json`。新增来源前检查其结构、许可和优先级；`redistribution_approved` 必须有真实授权依据，不能用它替代许可。
